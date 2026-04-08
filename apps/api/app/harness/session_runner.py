from __future__ import annotations

import asyncio
import importlib
import inspect
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import or_
from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.compat.mcp.service import MCPDisabledServerError, MCPInvalidToolError, MCPService
from app.compat.skills.service import SkillContentReadError, SkillLookupError, SkillService
from app.core.events import SessionEventBroker, SessionEventType
from app.db.models import (
    AssistantTranscriptSegmentKind,
    ChatGeneration,
    GenerationStatus,
    Message,
    MessageStatus,
    Session,
    SessionStatus,
    attachments_from_storage,
    utc_now,
)
from app.db.repositories import SessionRepository
from app.harness.continuations import clear_generation_continuation_state
from app.services.capabilities import CapabilityFacade
from app.services.chat_runtime import (
    ChatRuntime,
    ChatRuntimeConfigurationError,
    ChatRuntimeError,
    GenerationCallbacks,
    ToolCallRequest,
    ToolCallResult,
)
from app.services.runtime import (
    RuntimeArtifactPathError,
    RuntimeOperationError,
    RuntimePolicyViolationError,
    RuntimeService,
)
from app.services.session_generation import (
    GenerationCancelledError,
    SessionGenerationManager,
)

from . import generation_events as harness_generation_events
from . import semantic as harness_semantic
from . import trace as harness_trace
from . import transcript as harness_transcript
from .continuations import pause_tool_for_governance
from .tool_runtime_runner import ToolRuntimeLifecycleRunner

_SKILL_AUTOROUTE_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", re.IGNORECASE)
_SKILL_AUTOROUTE_SEPARATOR_RE = re.compile(r"[\s_\-./\\]+")
_SKILL_AUTOROUTE_DESCRIPTION_STOP_TOKENS = {
    "a",
    "an",
    "and",
    "for",
    "from",
    "helper",
    "into",
    "of",
    "or",
    "skill",
    "the",
    "to",
    "tool",
    "use",
    "when",
    "with",
}
_SKILL_AUTOROUTE_HIGH_CONFIDENCE_SCORE = 70
_SKILL_AUTOROUTE_MARGIN = 10


@dataclass(slots=True)
class HarnessGenerationPreparation:
    latest_message_text: str
    available_skills: list[Any]
    mcp_tool_inventory: list[dict[str, Any]]
    swarm_coordinator: Any
    prompt_assembly: Any


def chat_runtime_supports_mcp_tools(chat_runtime: ChatRuntime) -> bool:
    try:
        signature = inspect.signature(chat_runtime.generate_reply)
    except (TypeError, ValueError):
        return False
    return "mcp_tools" in signature.parameters


def chat_runtime_supports_harness_state(chat_runtime: ChatRuntime) -> bool:
    try:
        signature = inspect.signature(chat_runtime.generate_reply)
    except (TypeError, ValueError):
        return False
    return "harness_state" in signature.parameters


def build_swarm_coordinator(
    *,
    session: Any,
    chat_runtime: ChatRuntime,
    runtime_service: Any,
    skill_service: Any,
    mcp_service: Any,
    session_id: str,
    prompt_assembly: Any,
    generation_id: str,
    latest_message_text: str,
) -> Any:
    harness_swarm = importlib.import_module("app.harness.swarm")
    build_default_swarm_coordinator = harness_swarm.build_default_swarm_coordinator
    swarm_coordinator = build_default_swarm_coordinator(
        session_id=session_id,
        session_state=prompt_assembly.session_state,
        session=session,
        chat_runtime=chat_runtime,
        runtime_service=runtime_service,
        skill_service=skill_service,
        mcp_service=mcp_service,
    )
    swarm_coordinator.ensure_primary_agent(
        objective=latest_message_text,
        metadata={
            "generation_id": generation_id,
            "phase": prompt_assembly.session_state.current_phase,
        },
    )
    return swarm_coordinator


def build_generate_reply_kwargs(
    *,
    chat_runtime: ChatRuntime,
    prompt_assembly: Any,
    available_skills: list[Any],
    skill_context_prompt: str,
    execute_tool: Any,
    callbacks: GenerationCallbacks,
) -> dict[str, Any]:
    generate_reply_kwargs: dict[str, Any] = {
        "conversation_messages": prompt_assembly.conversation_messages,
        "available_skills": available_skills,
        "skill_context_prompt": skill_context_prompt,
        "execute_tool": execute_tool,
        "callbacks": callbacks,
    }
    if chat_runtime_supports_mcp_tools(chat_runtime):
        generate_reply_kwargs["mcp_tools"] = prompt_assembly.mcp_tool_inventory
    if chat_runtime_supports_harness_state(chat_runtime):
        generate_reply_kwargs["harness_state"] = prompt_assembly.session_state
    return generate_reply_kwargs


def _normalize_skill_autoroute_text(content: str) -> str:
    return re.sub(
        r"\s+",
        " ",
        _SKILL_AUTOROUTE_SEPARATOR_RE.sub(" ", content.casefold()),
    ).strip()


def _extract_skill_autoroute_tokens(
    content: str,
    *,
    filter_description_stop_tokens: bool,
) -> list[str]:
    tokens: list[str] = []
    for token in _SKILL_AUTOROUTE_TOKEN_RE.findall(_normalize_skill_autoroute_text(content)):
        normalized_token = token.casefold()
        if normalized_token.isascii() and len(normalized_token) < 2:
            continue
        if (
            filter_description_stop_tokens
            and normalized_token in _SKILL_AUTOROUTE_DESCRIPTION_STOP_TOKENS
        ):
            continue
        tokens.append(normalized_token)
    return tokens


def _skill_autoroute_display_name(skill: Any) -> str:
    directory_name = getattr(skill, "directory_name", None)
    if isinstance(directory_name, str) and directory_name.strip():
        return directory_name.strip()
    name = getattr(skill, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "unknown-skill"


def _skill_autoroute_aliases(skill: Any) -> list[str]:
    aliases: list[str] = []
    for raw_value in (getattr(skill, "directory_name", None), getattr(skill, "name", None)):
        if not isinstance(raw_value, str) or not raw_value.strip():
            continue
        normalized_value = _normalize_skill_autoroute_text(raw_value)
        if normalized_value and normalized_value not in aliases:
            aliases.append(normalized_value)
    return aliases


def _skill_autoroute_identifier(skill: Any) -> str:
    directory_name = getattr(skill, "directory_name", None)
    if isinstance(directory_name, str) and directory_name.strip():
        return directory_name.strip()
    return _skill_autoroute_display_name(skill)


def _score_skill_for_autoroute(
    *,
    skill: Any,
    normalized_context: str,
    context_tokens: set[str],
) -> tuple[int, str]:
    family = getattr(skill, "family", None)
    domain = getattr(skill, "domain", None)
    task_mode = getattr(skill, "task_mode", None)
    is_ctf_context = any(
        token in context_tokens
        for token in {"ctf", "flag", "buuoj", "xss", "sqli", "sql", "ssrf", "web"}
    )
    is_http_context = (
        any(
            token in context_tokens
            for token in {"http", "https", "web", "api", "xss", "sqli", "sql", "ssrf", "login"}
        )
        or "http://" in normalized_context
        or "https://" in normalized_context
    )
    specialized_focus = any(
        token in context_tokens
        for token in {
            "focus",
            "specific",
            "specialized",
            "analysis",
            "analyze",
            "audit",
            "漏洞",
            "分析",
            "专注",
        }
    )

    if family == "ctf" and domain == "web" and is_http_context:
        web_score = 78 if specialized_focus else 74
        return web_score, "ctf web prior from remote/http context"

    if family == "ctf" and task_mode == "dispatcher" and is_ctf_context:
        dispatcher_score = 72 if specialized_focus else (76 if is_http_context else 74)
        return dispatcher_score, "ctf dispatcher prior from challenge/web context"

    aliases = _skill_autoroute_aliases(skill)
    exact_alias_matches = [
        alias for alias in aliases if alias and f" {alias} " in f" {normalized_context} "
    ]
    if exact_alias_matches:
        return 100, f"matched explicit skill alias '{exact_alias_matches[0]}'"

    alias_token_matches: list[tuple[str, list[str]]] = []
    for alias in aliases:
        alias_tokens = _extract_skill_autoroute_tokens(
            alias,
            filter_description_stop_tokens=False,
        )
        if alias_tokens and all(token in context_tokens for token in alias_tokens):
            alias_token_matches.append((alias, alias_tokens))
    if alias_token_matches:
        alias, alias_tokens = max(alias_token_matches, key=lambda item: len(item[1]))
        return 70 + len(alias_tokens), f"matched alias tokens '{alias}'"

    description = getattr(skill, "description", None)
    if isinstance(description, str) and description.strip():
        description_tokens = _extract_skill_autoroute_tokens(
            description,
            filter_description_stop_tokens=True,
        )
        overlap = [token for token in description_tokens if token in context_tokens]
        if len(overlap) >= 2:
            overlap_preview = ", ".join(overlap[:3])
            return min(95, 60 + len(overlap) * 5), f"description overlap: {overlap_preview}"

    return 0, ""


def _resolve_autorouted_skill_candidate(
    *,
    available_skills: list[Any],
    latest_message_text: str,
    recent_context_text: str,
) -> tuple[Any | None, dict[str, object]]:
    if not available_skills:
        return None, {
            "decision": "skipped",
            "reason": "当前没有可用技能",
            "confidence": 0,
            "top_candidate": None,
            "candidates": [],
        }

    combined_context = "\n".join(
        part for part in [latest_message_text, recent_context_text] if part.strip()
    )
    normalized_context = _normalize_skill_autoroute_text(combined_context)
    context_tokens = set(
        _extract_skill_autoroute_tokens(
            combined_context,
            filter_description_stop_tokens=False,
        )
    )
    if not normalized_context and not context_tokens:
        return None, {
            "decision": "skipped",
            "reason": "当前消息没有可用于技能路由的上下文",
            "confidence": 0,
            "top_candidate": None,
            "candidates": [],
        }

    scored_candidates: list[tuple[int, str, Any, str]] = []
    for skill in available_skills:
        score, reason = _score_skill_for_autoroute(
            skill=skill,
            normalized_context=normalized_context,
            context_tokens=context_tokens,
        )
        if score <= 0:
            continue
        scored_candidates.append((score, _skill_autoroute_display_name(skill), skill, reason))

    if not scored_candidates:
        return None, {
            "decision": "skipped",
            "reason": "没有高置信技能匹配",
            "confidence": 0,
            "top_candidate": None,
            "candidates": [],
        }

    scored_candidates.sort(key=lambda item: (-item[0], item[1]))
    top_score, top_name, top_skill, top_reason = scored_candidates[0]
    runner_up = scored_candidates[1] if len(scored_candidates) > 1 else None
    candidate_preview = [
        {
            "skill": display_name,
            "confidence": score,
            "reason": reason,
        }
        for score, display_name, _skill, reason in scored_candidates[:3]
    ]

    if top_score < _SKILL_AUTOROUTE_HIGH_CONFIDENCE_SCORE:
        return None, {
            "decision": "skipped",
            "reason": "没有高置信技能匹配",
            "confidence": top_score,
            "top_candidate": top_name,
            "candidates": candidate_preview,
        }

    if runner_up is not None:
        runner_up_score, runner_up_name, _, _ = runner_up
        if runner_up_score >= _SKILL_AUTOROUTE_HIGH_CONFIDENCE_SCORE and (
            top_score - runner_up_score < _SKILL_AUTOROUTE_MARGIN
        ):
            return None, {
                "decision": "skipped",
                "reason": f"存在多个高置信技能候选（{top_name}, {runner_up_name}）",
                "confidence": top_score,
                "top_candidate": top_name,
                "candidates": candidate_preview,
            }

    return top_skill, {
        "decision": "selected",
        "reason": top_reason,
        "confidence": top_score,
        "top_candidate": top_name,
        "candidates": candidate_preview,
    }


async def build_autorouted_skill_context(
    *,
    available_skills: list[Any],
    latest_message_text: str,
    recent_context_text: str,
    execute_tool: Any,
) -> tuple[str | None, list[dict[str, object]], dict[str, object]]:
    selected_skill, route_report = _resolve_autorouted_skill_candidate(
        available_skills=available_skills,
        latest_message_text=latest_message_text,
        recent_context_text=recent_context_text,
    )
    route_reason = str(route_report.get("reason") or "")
    raw_route_confidence = route_report.get("confidence")
    route_confidence = (
        int(raw_route_confidence) if isinstance(raw_route_confidence, int | float | str) else 0
    )
    route_candidates = route_report.get("candidates")
    candidate_list = route_candidates if isinstance(route_candidates, list) else []
    if selected_skill is None:
        skipped_entry = {
            "state": "skill.autoroute.skipped",
            "reason": route_reason,
            "confidence": route_confidence,
            "top_candidate": route_report.get("top_candidate"),
            "candidates": candidate_list,
        }
        return None, [skipped_entry], skipped_entry

    skill_identifier = _skill_autoroute_identifier(selected_skill)
    skill_display_name = _skill_autoroute_display_name(selected_skill)
    selected_entry = {
        "state": "skill.autoroute.selected",
        "summary": f"自动选择 {skill_display_name}",
        "skill": skill_display_name,
        "reason": route_reason,
        "confidence": route_confidence,
        "top_candidate": route_report.get("top_candidate"),
        "candidates": candidate_list,
    }
    executing_entry = {
        "state": "skill.autoroute.executing",
        "skill": skill_display_name,
        "reason": route_reason,
        "confidence": route_confidence,
        "top_candidate": route_report.get("top_candidate"),
        "candidates": candidate_list,
    }
    try:
        tool_result = await execute_tool(
            ToolCallRequest(
                tool_call_id=f"autoroute-skill-{uuid4()}",
                tool_name="execute_skill",
                arguments={
                    "skill_name_or_id": skill_identifier,
                    "current_prompt": latest_message_text,
                    "user_goal": latest_message_text,
                    "use_selected_skill_set": True,
                },
            )
        )
    except ChatRuntimeError as exc:
        failed_entry = {
            "state": "skill.autoroute.failed",
            "summary": f"自动预载技能失败：{skill_display_name}（{exc}）",
            "skill": skill_display_name,
            "reason": route_reason,
            "confidence": route_confidence,
            "top_candidate": route_report.get("top_candidate"),
            "candidates": candidate_list,
            "error": str(exc),
        }
        return None, [selected_entry, executing_entry, failed_entry], failed_entry

    skill_payload = tool_result.payload.get("skill")
    prepared_primary_payload = tool_result.payload.get("prepared_primary_skill")
    execution_payload = tool_result.payload.get("execution")
    resolved_skill_payload = (
        prepared_primary_payload
        if isinstance(prepared_primary_payload, dict)
        else skill_payload if isinstance(skill_payload, dict) else None
    )
    if isinstance(resolved_skill_payload, dict):
        resolved_skill_name = str(
            resolved_skill_payload.get("directory_name")
            or resolved_skill_payload.get("name")
            or skill_display_name
        )
        selected_entry["summary"] = f"自动选择 {resolved_skill_name}"
        selected_entry["skill"] = resolved_skill_name
        executing_entry["skill"] = resolved_skill_name
    finished_entry = {
        "state": "skill.autoroute.finished",
        "skill": selected_entry.get("skill", skill_display_name),
        "reason": route_reason,
        "confidence": route_confidence,
        "top_candidate": route_report.get("top_candidate"),
        "candidates": candidate_list,
        "execution": execution_payload if isinstance(execution_payload, dict) else {},
    }
    prepared_context_prompt = tool_result.payload.get("prepared_context_prompt")
    if isinstance(prepared_context_prompt, str) and prepared_context_prompt.strip():
        return (
            prepared_context_prompt.strip(),
            [selected_entry, executing_entry, finished_entry],
            {**finished_entry, "context_injected": True},
        )

    if not isinstance(skill_payload, dict):
        return (
            f"## Auto-selected skill: {skill_display_name}",
            [selected_entry, executing_entry, finished_entry],
            finished_entry,
        )

    prepared_prompt = (
        execution_payload.get("prepared_prompt") if isinstance(execution_payload, dict) else None
    )
    if isinstance(prepared_prompt, str) and prepared_prompt.strip():
        finished_entry["skill"] = skill_display_name
        return (
            prepared_prompt.strip(),
            [selected_entry, executing_entry, finished_entry],
            {**finished_entry, "context_injected": True},
        )

    directory_name = skill_payload.get("directory_name")
    resolved_skill_name = (
        str(directory_name).strip()
        if isinstance(directory_name, str) and directory_name.strip()
        else skill_display_name
    )
    description = skill_payload.get("description")
    content = skill_payload.get("content")
    truncated_content = content.strip() if isinstance(content, str) else ""
    if len(truncated_content) > 4000:
        truncated_content = truncated_content[:4000].rstrip() + "\n...[truncated]"

    context_lines = [
        f"## Auto-selected skill: {resolved_skill_name}",
        f"Confidence: {route_confidence}",
        f"Reason: {route_reason}",
        (
            "Prompt provenance: this preloaded skill fragment was injected by the "
            "server-side skill router before the first model turn."
        ),
        "Use this preloaded skill guidance proactively before deciding on the next tool or answer.",
    ]
    if isinstance(description, str) and description.strip():
        context_lines.append(f"Description: {description.strip()}")
    if truncated_content:
        context_lines.extend(["", truncated_content])

    finished_entry["skill"] = resolved_skill_name
    return (
        "\n".join(context_lines),
        [selected_entry, executing_entry, finished_entry],
        {**finished_entry, "context_injected": True},
    )


def build_tool_executor(
    *,
    session: Session,
    assistant_message: Message,
    repository: SessionRepository,
    event_broker: SessionEventBroker,
    generation_manager: SessionGenerationManager,
    runtime_service: RuntimeService,
    skill_service: SkillService,
    mcp_service: MCPService,
    mcp_tool_inventory: list[dict[str, Any]] | None = None,
    session_state: Any | None = None,
    swarm_coordinator: Any | None = None,
) -> Any:
    executor_runtime = importlib.import_module("app.harness.executor").build_tool_runtime(
        skill_service=skill_service,
        session_id=session.id,
        mcp_tool_inventory=mcp_tool_inventory,
        include_swarm_tools=swarm_coordinator is not None,
    )
    harness_executor = importlib.import_module("app.harness.executor")
    harness_tool_scheduling = importlib.import_module("app.harness.tool_scheduling")
    lifecycle = ToolRuntimeLifecycleRunner(
        session=session,
        assistant_message=assistant_message,
        repository=repository,
        event_broker=event_broker,
        session_state=session_state,
        publish_assistant_trace=harness_trace.publish_assistant_trace,
    )

    async def execute_tool(tool_request: ToolCallRequest) -> ToolCallResult:
        prepared = harness_executor.prepare_tool_execution(
            runtime=executor_runtime,
            tool_request=tool_request,
            session=session,
            assistant_message=assistant_message,
            runtime_service=runtime_service,
            skill_service=skill_service,
            mcp_service=mcp_service,
            session_state=session_state,
            swarm_coordinator=swarm_coordinator,
        )
        prepared = await harness_executor.apply_pre_tool_hooks(
            runtime=executor_runtime,
            prepared=prepared,
            tool_request=tool_request,
        )
        tool = prepared.tool
        decision = prepared.decision
        governance_metadata = prepared.governance_metadata
        tool_call_metadata = prepared.tool_call_metadata
        started_payload = prepared.started_payload

        await lifecycle.publish_tool_started(
            tool_request,
            tool_call_metadata=tool_call_metadata,
            governance_metadata=governance_metadata,
            started_payload=started_payload,
            trace_entry=prepared.trace_entry,
        )

        async def pause_for_governance() -> dict[str, object]:
            if decision is None:
                raise ChatRuntimeError("Missing governance decision for tool execution.")
            if assistant_message.generation_id is None:
                raise ChatRuntimeError("Tool pause requires an active generation.")
            try:
                return await pause_tool_for_governance(
                    repository=repository,
                    session=session,
                    assistant_message=assistant_message,
                    tool_request=tool_request,
                    decision=decision,
                    governance_metadata=governance_metadata,
                    tool_call_metadata=tool_call_metadata,
                    started_payload=started_payload,
                    event_broker=event_broker,
                    generation_manager=generation_manager,
                    publish_assistant_trace=harness_trace.publish_assistant_trace,
                )
            except RuntimeError as exc:
                raise ChatRuntimeError(str(exc)) from exc

        async def _run_registry_tool() -> Any:
            if tool is None:
                raise ChatRuntimeError(f"Unsupported tool requested: {tool_request.tool_name}.")
            if decision is None:
                raise ChatRuntimeError("Missing governance decision for tool execution.")
            return await harness_executor.run_tool_with_hooks(
                runtime=executor_runtime,
                prepared=prepared,
                tool_request=tool_request,
            )

        if tool is None:
            error_message = f"Unsupported tool requested: {tool_request.tool_name}."
            await lifecycle.publish_tool_failed(
                tool_request,
                started_payload=started_payload,
                error_message=error_message,
            )
            raise ChatRuntimeError(error_message)

        if decision is not None and not decision.allowed:
            if decision.action in {"require_approval", "require_scope_confirmation"}:
                resolution_payload = await pause_for_governance()
                approved = bool(resolution_payload.get("approved"))
                resolution_data = resolution_payload.get("resolution_payload")
                scope_confirmed = False
                if isinstance(resolution_data, dict):
                    scope_confirmed = bool(resolution_data.get("scope_confirmed"))
                scope_confirmed = scope_confirmed or bool(resolution_payload.get("scope_confirmed"))
                if decision.action == "require_approval" and not approved:
                    error_message = decision.reason or "Tool approval was not granted."
                    await lifecycle.publish_tool_failed(
                        tool_request,
                        started_payload=started_payload,
                        error_message=error_message,
                    )
                    raise ChatRuntimeError(error_message)
                if decision.action == "require_scope_confirmation" and not scope_confirmed:
                    error_message = decision.reason or "Scope confirmation was not granted."
                    await lifecycle.publish_tool_failed(
                        tool_request,
                        started_payload=started_payload,
                        error_message=error_message,
                    )
                    raise ChatRuntimeError(error_message)
            else:
                error_message = decision.reason or "Tool use denied by governance."
                await lifecycle.publish_tool_failed(
                    tool_request,
                    started_payload=started_payload,
                    error_message=error_message,
                )
                raise ChatRuntimeError(error_message)

        try:
            tool_result = await _run_registry_tool()
        except (
            MCPDisabledServerError,
            MCPInvalidToolError,
            RuntimeArtifactPathError,
            RuntimeOperationError,
            RuntimePolicyViolationError,
            SkillLookupError,
            SkillContentReadError,
            ValidationError,
        ) as exc:
            error_artifacts = (
                await harness_executor.notify_tool_execution_error(
                    runtime=executor_runtime,
                    prepared=prepared,
                    tool_request=tool_request,
                    error=exc,
                )
                if decision is not None
                else None
            )
            await lifecycle.publish_tool_failed(
                tool_request,
                started_payload=started_payload,
                error_message=str(exc),
                error_artifacts=error_artifacts,
            )
            raise ChatRuntimeError(str(exc)) from exc
        except Exception as exc:
            error_artifacts = (
                await harness_executor.notify_tool_execution_error(
                    runtime=executor_runtime,
                    prepared=prepared,
                    tool_request=tool_request,
                    error=exc,
                )
                if decision is not None
                else None
            )
            await lifecycle.publish_tool_failed(
                tool_request,
                started_payload=started_payload,
                error_message=str(exc),
                error_artifacts=error_artifacts,
            )
            raise ChatRuntimeError(str(exc)) from exc

        try:
            return await lifecycle.persist_tool_success(
                tool_request,
                tool_result=tool_result,
                started_payload=started_payload,
            )
        except Exception as exc:
            await lifecycle.publish_tool_failed(
                tool_request,
                started_payload=started_payload,
                error_message=str(exc),
            )
            raise ChatRuntimeError(str(exc)) from exc

    async def batch_execute(tool_requests: list[ToolCallRequest]) -> list[ToolCallResult]:
        prepared_executions = [
            harness_executor.prepare_tool_execution(
                runtime=executor_runtime,
                tool_request=tool_request,
                session=session,
                assistant_message=assistant_message,
                runtime_service=runtime_service,
                skill_service=skill_service,
                mcp_service=mcp_service,
                session_state=session_state,
                swarm_coordinator=swarm_coordinator,
            )
            for tool_request in tool_requests
        ]
        phases = harness_tool_scheduling.build_tool_schedule(tool_requests, prepared_executions)
        results: list[ToolCallResult | None] = [None] * len(tool_requests)

        for phase in phases:
            if phase.lane != "readonly_parallel" or len(phase.items) <= 1:
                for scheduled in phase.items:
                    results[scheduled.order] = await execute_tool(scheduled.tool_request)
                continue
            for order, result in await lifecycle.execute_readonly_parallel_phase(
                phase=phase,
                runtime=executor_runtime,
                executor_module=harness_executor,
            ):
                results[order] = result

        return [result for result in results if result is not None]

    setattr(execute_tool, "__batch_execute__", batch_execute)
    return execute_tool


def recover_abandoned_generations(db_engine: Engine) -> int:
    current_time = utc_now()
    with DBSession(db_engine) as db_session:
        repository = SessionRepository(db_session)
        abandoned_ids = list(
            db_session.exec(
                select(ChatGeneration.id)
                .where(ChatGeneration.status == GenerationStatus.RUNNING)
                .where(
                    or_(
                        col(ChatGeneration.lease_expires_at).is_(None),
                        col(ChatGeneration.lease_expires_at) < current_time,
                    )
                )
                .order_by(col(ChatGeneration.created_at).asc(), col(ChatGeneration.id).asc())
            ).all()
        )
        recovered_count = repository.recover_abandoned_generations(now=current_time)
        for generation_id in abandoned_ids:
            generation = repository.get_generation(generation_id)
            if generation is None or generation.status != GenerationStatus.QUEUED:
                continue
            if generation.metadata_json.get("pending_continuation") is None and not isinstance(
                generation.metadata_json.get("pause_state"), dict
            ):
                continue
            assistant_message = repository.get_message(generation.assistant_message_id)
            clear_generation_continuation_state(
                repository,
                generation,
                assistant_message,
                abort_reason=(
                    "Generation recovery abandoned an in-memory continuation after restart."
                ),
            )
        return recovered_count


async def _mark_queued_generations_failed(
    db_engine: Engine,
    *,
    session_id: str,
    error_message: str,
) -> None:
    with DBSession(db_engine) as worker_db_session:
        repository = SessionRepository(worker_db_session)
        queued_generations = repository.list_generations(
            session_id,
            statuses={GenerationStatus.QUEUED},
        )
        for generation in queued_generations:
            assistant_message = repository.get_message(generation.assistant_message_id)
            clear_generation_continuation_state(
                repository,
                generation,
                assistant_message,
                abort_reason=error_message,
            )
            repository.mark_generation_failed(generation, error_message)
            if assistant_message is not None:
                repository.update_message(
                    assistant_message,
                    status=MessageStatus.FAILED,
                    error_message=error_message,
                )
                harness_trace.record_generation_step(
                    repository,
                    assistant_message=assistant_message,
                    kind="status",
                    phase="failed",
                    status="failed",
                    state="failed",
                    label="Generation failed",
                    safe_summary=error_message,
                    ended_at=utc_now(),
                    metadata_json={"generation_id": generation.id, "error": error_message},
                )
            repository.close_open_generation_steps(generation.id, status="failed", state="failed")


async def process_generation(
    *,
    db_engine: Engine,
    session_id: str,
    generation_id: str,
    event_broker: SessionEventBroker,
    generation_manager: SessionGenerationManager,
    chat_runtime: ChatRuntime,
    runtime_service: RuntimeService,
    skill_service: SkillService,
    mcp_service: MCPService,
) -> str:
    active_session_state: Any | None = None
    with DBSession(db_engine) as worker_db_session:
        repository = SessionRepository(worker_db_session)
        generation = repository.get_generation(generation_id)
        if generation is None:
            raise ChatRuntimeError("Generation record was not found.")
        initial_assistant_message = repository.get_message(generation.assistant_message_id)
        if initial_assistant_message is None:
            raise ChatRuntimeError("Assistant message placeholder was not found.")

    await generation_manager.begin_generation(
        session_id,
        generation_id=generation_id,
        assistant_message_id=initial_assistant_message.id,
    )
    cancel_event = await generation_manager.get_cancel_event(session_id)

    try:
        with DBSession(db_engine) as worker_db_session:
            repository = SessionRepository(worker_db_session)
            session = repository.get_session(session_id)
            generation = repository.get_generation(generation_id)
            assistant_message = repository.get_message(initial_assistant_message.id)
            if session is None or generation is None or assistant_message is None:
                raise ChatRuntimeError("Generation state could not be reloaded.")
            loaded_assistant_message = assistant_message
            user_message = (
                repository.get_message(generation.user_message_id)
                if generation.user_message_id is not None
                else None
            )
            token_budget = generation.metadata_json.get("token_budget")
            total_token_budget = token_budget if isinstance(token_budget, int) else 12_000

            latest_message_text = (
                user_message.content
                if user_message is not None
                else loaded_assistant_message.content
            )
            capability_facade = CapabilityFacade(
                skill_service=skill_service,
                mcp_service=mcp_service,
            )
            harness_memory = importlib.import_module("app.harness.memory")
            harness_prompts = importlib.import_module("app.harness.prompts")
            HarnessMemoryService = harness_memory.HarnessMemoryService
            HarnessPromptAssembler = harness_prompts.HarnessPromptAssembler
            prompt_assembler = HarnessPromptAssembler(
                capability_facade=capability_facade,
                skill_service=skill_service,
                memory_service=HarnessMemoryService(),
            )
            prompt_assembly = prompt_assembler.build(
                session=session,
                repository=repository,
                user_message=user_message,
                assistant_message=loaded_assistant_message,
                branch_id=generation.branch_id,
                total_token_budget=total_token_budget,
            )
            active_session_state = prompt_assembly.session_state
            latest_message_text = prompt_assembly.latest_message_text
            available_skills = prompt_assembly.available_skills
            mcp_tool_inventory = prompt_assembly.mcp_tool_inventory
            swarm_coordinator = build_swarm_coordinator(
                session=session,
                chat_runtime=chat_runtime,
                runtime_service=runtime_service,
                skill_service=skill_service,
                mcp_service=mcp_service,
                session_id=session.id,
                prompt_assembly=prompt_assembly,
                generation_id=generation.id,
                latest_message_text=latest_message_text,
            )
            execute_tool = build_tool_executor(
                session=session,
                assistant_message=loaded_assistant_message,
                repository=repository,
                event_broker=event_broker,
                generation_manager=generation_manager,
                runtime_service=runtime_service,
                skill_service=skill_service,
                mcp_service=mcp_service,
                mcp_tool_inventory=mcp_tool_inventory,
                session_state=prompt_assembly.session_state,
                swarm_coordinator=swarm_coordinator,
            )
            conversation_history = prompt_assembly.conversation_history
            skill_context_prompt = prompt_assembly.skill_context_prompt

            await harness_generation_events.publish_generation_started(
                event_broker,
                session_id=session.id,
                generation_id=generation.id,
                user_message_id=generation.user_message_id,
                assistant_message_id=assistant_message.id,
                queued_prompt_count=repository.queue_size(session.id),
            )
            harness_trace.record_generation_step(
                repository,
                assistant_message=assistant_message,
                kind="status",
                phase="planning",
                status="running",
                state="started",
                label="开始生成",
                safe_summary=None,
                metadata_json={"generation_id": generation.id},
            )
            await harness_trace.publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=assistant_message,
                entry={"state": "generation.started", "generation_id": generation.id},
            )

            await harness_trace.publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=assistant_message,
                entry={"state": "skill.autoroute.started"},
            )
            (
                autorouted_skill_context,
                autoroute_trace_entries,
                autoroute_trace_entry,
            ) = await build_autorouted_skill_context(
                available_skills=available_skills,
                latest_message_text=latest_message_text,
                recent_context_text="\n\n".join(
                    message.content
                    for message in conversation_history[-6:]
                    if message.content.strip()
                ),
                execute_tool=execute_tool,
            )
            for trace_entry in autoroute_trace_entries:
                await harness_trace.publish_assistant_trace(
                    repository,
                    event_broker,
                    session_id=session.id,
                    assistant_message=assistant_message,
                    entry=trace_entry,
                )
            if autorouted_skill_context:
                skill_context_prompt = "\n\n".join(
                    part
                    for part in [skill_context_prompt, autorouted_skill_context]
                    if part.strip()
                )
            generation_metadata = dict(generation.metadata_json)
            existing_prompt_provenance = generation_metadata.get("prompt_provenance")
            prompt_provenance = (
                dict(existing_prompt_provenance)
                if isinstance(existing_prompt_provenance, dict)
                else {}
            )
            generation_metadata["prompt_provenance"] = {
                **prompt_provenance,
                "autorouted_skill": {
                    "state": autoroute_trace_entry.get("state"),
                    "skill": autoroute_trace_entry.get("skill"),
                    "confidence": autoroute_trace_entry.get("confidence"),
                    "reason": autoroute_trace_entry.get("reason"),
                    "top_candidate": autoroute_trace_entry.get("top_candidate"),
                    "candidates": autoroute_trace_entry.get("candidates", []),
                    "context_injected": bool(autorouted_skill_context),
                },
            }
            repository.update_generation(generation, metadata_json=generation_metadata)

            raw_streamed_content = loaded_assistant_message.content
            streamed_content = harness_transcript.project_visible_stream_content(
                raw_streamed_content
            )
            current_generation_id = generation.id
            force_new_output_segment = False
            applied_injection_count = 0

            async def consume_context_injections() -> list[str]:
                return await generation_manager.drain_injections(
                    session.id,
                    generation_id=current_generation_id,
                )

            async def on_context_injection_applied(injections: list[str]) -> None:
                nonlocal force_new_output_segment, applied_injection_count
                if not injections:
                    return
                force_new_output_segment = True
                applied_injection_count += len(injections)
                harness_transcript.append_transcript_segment(
                    repository,
                    assistant_message=loaded_assistant_message,
                    kind=AssistantTranscriptSegmentKind.STATUS,
                    status="running",
                    text=(
                        f"已注入 {len(injections)} 条人工上下文，当前生成将在安全检查点后继续推理。"
                    ),
                    metadata_json={"kind": "context_injection", "count": len(injections)},
                )
                await harness_generation_events.publish_message_event(
                    event_broker,
                    event_type=SessionEventType.MESSAGE_UPDATED,
                    session_id=session.id,
                    message=loaded_assistant_message,
                )

            async def on_text_delta(delta: str) -> None:
                nonlocal raw_streamed_content, streamed_content, force_new_output_segment
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                raw_streamed_content += delta
                next_streamed_content = harness_transcript.project_visible_stream_content(
                    raw_streamed_content
                )
                if next_streamed_content == streamed_content:
                    return

                sanitized_delta = (
                    next_streamed_content[len(streamed_content) :]
                    if next_streamed_content.startswith(streamed_content)
                    else next_streamed_content
                )
                is_incremental_output = next_streamed_content.startswith(streamed_content)
                append_to_current = is_incremental_output and not force_new_output_segment
                streamed_content = next_streamed_content
                repository.update_message(
                    loaded_assistant_message,
                    content=streamed_content,
                    status=MessageStatus.STREAMING,
                )
                harness_transcript.append_output_transcript_delta(
                    repository,
                    assistant_message=loaded_assistant_message,
                    delta_text=sanitized_delta,
                    status="running",
                    append_to_current=append_to_current,
                )
                force_new_output_segment = False
                output_step = harness_trace.get_or_create_output_step(
                    repository,
                    assistant_message=loaded_assistant_message,
                )
                if output_step is not None:
                    repository.append_generation_step_delta(output_step, sanitized_delta)
                await harness_generation_events.publish_message_event(
                    event_broker,
                    event_type=SessionEventType.MESSAGE_DELTA,
                    session_id=session.id,
                    message=loaded_assistant_message,
                    delta=sanitized_delta,
                )
                await harness_generation_events.publish_message_event(
                    event_broker,
                    event_type=SessionEventType.MESSAGE_UPDATED,
                    session_id=session.id,
                    message=loaded_assistant_message,
                )

            async def on_summary(summary: str) -> None:
                if summary.strip():
                    semantic_snapshot = harness_semantic.drain_semantic_snapshot(
                        repository,
                        assistant_message=loaded_assistant_message,
                        session_state=prompt_assembly.session_state,
                    )
                    await harness_trace.publish_assistant_summary(
                        repository,
                        event_broker,
                        session_id=session.id,
                        assistant_message=loaded_assistant_message,
                        summary=summary.strip(),
                        semantic_snapshot=semantic_snapshot,
                    )

            generate_reply_kwargs = build_generate_reply_kwargs(
                chat_runtime=chat_runtime,
                prompt_assembly=prompt_assembly,
                available_skills=available_skills,
                skill_context_prompt=skill_context_prompt,
                execute_tool=execute_tool,
                callbacks=GenerationCallbacks(
                    on_text_delta=on_text_delta,
                    on_summary=on_summary,
                    is_cancelled=cancel_event.is_set,
                    consume_context_injections=consume_context_injections,
                    on_context_injection_applied=on_context_injection_applied,
                ),
            )

            final_content = await chat_runtime.generate_reply(
                (
                    user_message.content
                    if user_message is not None
                    else loaded_assistant_message.content
                ),
                (
                    []
                    if user_message is None
                    else attachments_from_storage(user_message.attachments_json)
                ),
                **generate_reply_kwargs,
            )
            final_content = harness_transcript.sanitize_persisted_assistant_text(final_content) or (
                "模型已完成分析，但没有返回可展示的最终答复。"
            )

            if cancel_event.is_set():
                raise asyncio.CancelledError

            aggregated_final_content = final_content
            if applied_injection_count > 0 and streamed_content:
                aggregated_final_content = (
                    streamed_content
                    if streamed_content.endswith(final_content)
                    else f"{streamed_content}{final_content}"
                )

            repository.update_message(
                loaded_assistant_message,
                content=aggregated_final_content,
                status=MessageStatus.COMPLETED,
                error_message="",
            )
            semantic_snapshot = harness_semantic.drain_semantic_snapshot(
                repository,
                assistant_message=loaded_assistant_message,
                session_state=prompt_assembly.session_state,
            )
            final_delta = (
                aggregated_final_content[len(streamed_content) :]
                if aggregated_final_content.startswith(streamed_content)
                else aggregated_final_content
            )
            final_is_incremental = (
                aggregated_final_content.startswith(streamed_content)
                and not force_new_output_segment
            )
            harness_transcript.append_output_transcript_delta(
                repository,
                assistant_message=loaded_assistant_message,
                delta_text=final_delta,
                status="completed",
                append_to_current=final_is_incremental,
            )
            force_new_output_segment = False
            repository.mark_generation_completed(generation)
            if aggregated_final_content != streamed_content:
                if final_delta:
                    await harness_generation_events.publish_message_event(
                        event_broker,
                        event_type=SessionEventType.MESSAGE_DELTA,
                        session_id=session.id,
                        message=loaded_assistant_message,
                        delta=final_delta,
                    )
                await harness_generation_events.publish_message_event(
                    event_broker,
                    event_type=SessionEventType.MESSAGE_UPDATED,
                    session_id=session.id,
                    message=loaded_assistant_message,
                )
            output_step = harness_trace.get_or_create_output_step(
                repository,
                assistant_message=loaded_assistant_message,
            )
            if output_step is not None:
                if aggregated_final_content != output_step.delta_text:
                    repository.update_generation_step(
                        output_step,
                        delta_text=aggregated_final_content,
                    )
                repository.update_generation_step(
                    output_step,
                    phase="synthesis",
                    status="completed",
                    state="completed",
                    ended_at=utc_now(),
                )
            await harness_generation_events.publish_attack_graph_updated(
                event_broker,
                session_id=session.id,
                assistant_message=loaded_assistant_message,
                semantic_snapshot=semantic_snapshot,
            )
            await harness_trace.publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=loaded_assistant_message,
                entry={
                    "state": "generation.completed",
                    "generation_id": generation.id,
                    **({"semantic_state": semantic_snapshot} if semantic_snapshot else {}),
                },
            )
            await harness_generation_events.publish_message_event(
                event_broker,
                event_type=SessionEventType.MESSAGE_COMPLETED,
                session_id=session.id,
                message=loaded_assistant_message,
            )
            harness_trace.record_generation_step(
                repository,
                assistant_message=loaded_assistant_message,
                kind="status",
                phase="completed",
                status="completed",
                state="completed",
                label="生成完成",
                safe_summary=None,
                ended_at=utc_now(),
                metadata_json={"generation_id": generation.id},
            )
            repository.close_open_generation_steps(
                generation.id, status="completed", state="completed"
            )
            return loaded_assistant_message.id
    except asyncio.CancelledError as exc:
        with DBSession(db_engine) as worker_db_session:
            repository = SessionRepository(worker_db_session)
            generation = repository.get_generation(generation_id)
            assistant_message = None
            if generation is not None:
                assistant_message = repository.get_message(generation.assistant_message_id)
                clear_generation_continuation_state(
                    repository,
                    generation,
                    assistant_message,
                    abort_reason="Active generation was cancelled.",
                )
                repository.cancel_generation(
                    generation,
                    error_message="Active generation was cancelled.",
                )
            if assistant_message is not None:
                repository.update_message(
                    assistant_message,
                    status=MessageStatus.CANCELLED,
                    error_message="Active generation was cancelled.",
                )
                transcript_segments = harness_transcript.message_transcript_segments(
                    repository,
                    assistant_message,
                )
                output_segment = harness_transcript.find_transcript_segment(
                    transcript_segments,
                    kind=AssistantTranscriptSegmentKind.OUTPUT,
                )
                if output_segment is not None:
                    harness_transcript.update_transcript_segment(
                        repository,
                        assistant_message=assistant_message,
                        segment=output_segment,
                        status="cancelled",
                    )
                semantic_snapshot = harness_semantic.drain_semantic_snapshot(
                    repository,
                    assistant_message=assistant_message,
                    session_state=active_session_state,
                )
                await harness_trace.publish_assistant_trace(
                    repository,
                    event_broker,
                    session_id=session_id,
                    assistant_message=assistant_message,
                    entry={
                        "state": "generation.cancelled",
                        "generation_id": generation_id,
                        **({"semantic_state": semantic_snapshot} if semantic_snapshot else {}),
                    },
                )
                await harness_generation_events.publish_attack_graph_updated(
                    event_broker,
                    session_id=session_id,
                    assistant_message=assistant_message,
                    semantic_snapshot=semantic_snapshot,
                )
                harness_trace.record_generation_step(
                    repository,
                    assistant_message=assistant_message,
                    kind="status",
                    phase="cancelled",
                    status="cancelled",
                    state="cancelled",
                    label="生成已取消",
                    safe_summary=None,
                    ended_at=utc_now(),
                    metadata_json={"generation_id": generation_id},
                )
                repository.close_open_generation_steps(
                    generation_id,
                    status="cancelled",
                    state="cancelled",
                )
                await harness_generation_events.publish_generation_cancelled(
                    event_broker,
                    session_id=session_id,
                    generation_id=generation_id,
                    assistant_message_id=assistant_message.id,
                )
        raise GenerationCancelledError("Active generation was cancelled.") from exc
    except (ChatRuntimeConfigurationError, ChatRuntimeError) as exc:
        with DBSession(db_engine) as worker_db_session:
            repository = SessionRepository(worker_db_session)
            generation = repository.get_generation(generation_id)
            assistant_message = None
            if generation is not None:
                assistant_message = repository.get_message(generation.assistant_message_id)
                clear_generation_continuation_state(
                    repository,
                    generation,
                    assistant_message,
                    abort_reason=str(exc),
                )
                repository.mark_generation_failed(generation, str(exc))
            if assistant_message is not None:
                repository.update_message(
                    assistant_message,
                    status=MessageStatus.FAILED,
                    error_message=str(exc),
                )
                transcript_segments = harness_transcript.message_transcript_segments(
                    repository,
                    assistant_message,
                )
                output_segment = harness_transcript.find_transcript_segment(
                    transcript_segments,
                    kind=AssistantTranscriptSegmentKind.OUTPUT,
                )
                if output_segment is not None:
                    harness_transcript.update_transcript_segment(
                        repository,
                        assistant_message=assistant_message,
                        segment=output_segment,
                        status="failed",
                    )
                semantic_snapshot = harness_semantic.drain_semantic_snapshot(
                    repository,
                    assistant_message=assistant_message,
                    session_state=active_session_state,
                )
                await harness_trace.publish_assistant_trace(
                    repository,
                    event_broker,
                    session_id=session_id,
                    assistant_message=assistant_message,
                    entry={
                        "state": "generation.failed",
                        "generation_id": generation_id,
                        "error": str(exc),
                        **({"semantic_state": semantic_snapshot} if semantic_snapshot else {}),
                    },
                )
                await harness_generation_events.publish_attack_graph_updated(
                    event_broker,
                    session_id=session_id,
                    assistant_message=assistant_message,
                    semantic_snapshot=semantic_snapshot,
                )
                harness_trace.record_generation_step(
                    repository,
                    assistant_message=assistant_message,
                    kind="status",
                    phase="failed",
                    status="failed",
                    state="failed",
                    label="Generation failed",
                    safe_summary=str(exc),
                    ended_at=utc_now(),
                    metadata_json={"generation_id": generation_id, "error": str(exc)},
                )
                repository.close_open_generation_steps(
                    generation_id,
                    status="failed",
                    state="failed",
                )
                await harness_generation_events.publish_generation_failed(
                    event_broker,
                    session_id=session_id,
                    generation_id=generation_id,
                    assistant_message_id=assistant_message.id,
                    error_message=str(exc),
                )
        raise
    finally:
        await generation_manager.clear_current_generation(session_id, generation_id)


async def run_session_worker(
    *,
    db_engine: Engine,
    session_id: str,
    event_broker: SessionEventBroker,
    generation_manager: SessionGenerationManager,
    chat_runtime: ChatRuntime,
    runtime_service: RuntimeService,
    skill_service: SkillService,
    mcp_service: MCPService,
) -> None:
    current_task = asyncio.current_task()
    if current_task is None:
        return
    worker_id = f"session-worker-{uuid4()}"

    terminal_error: ChatRuntimeError | ChatRuntimeConfigurationError | None = None

    try:
        while True:
            with DBSession(db_engine) as worker_db_session:
                repository = SessionRepository(worker_db_session)
                generation = repository.claim_next_generation(session_id, worker_id=worker_id)

            if generation is None:
                break

            try:
                assistant_message_id = await process_generation(
                    db_engine=db_engine,
                    session_id=session_id,
                    generation_id=generation.id,
                    event_broker=event_broker,
                    generation_manager=generation_manager,
                    chat_runtime=chat_runtime,
                    runtime_service=runtime_service,
                    skill_service=skill_service,
                    mcp_service=mcp_service,
                )
                await generation_manager.resolve_future(
                    session_id,
                    generation.id,
                    assistant_message_id,
                )
            except GenerationCancelledError as exc:
                await generation_manager.reject_future(session_id, generation.id, exc)
                continue
            except ChatRuntimeConfigurationError as exc:
                terminal_error = exc
                await generation_manager.reject_continuation_futures(session_id, exc)
                await generation_manager.reject_future(session_id, generation.id, exc)
                await generation_manager.reject_pending(session_id, exc)
                await _mark_queued_generations_failed(
                    db_engine,
                    session_id=session_id,
                    error_message=str(exc),
                )
                break
            except ChatRuntimeError as exc:
                terminal_error = exc
                await generation_manager.reject_continuation_futures(session_id, exc)
                await generation_manager.reject_future(session_id, generation.id, exc)
                await generation_manager.reject_pending(session_id, exc)
                await _mark_queued_generations_failed(
                    db_engine,
                    session_id=session_id,
                    error_message=str(exc),
                )
                break
            except Exception as exc:
                terminal_error = ChatRuntimeError(str(exc))
                await generation_manager.reject_continuation_futures(session_id, terminal_error)
                await generation_manager.reject_future(session_id, generation.id, terminal_error)
                await generation_manager.reject_pending(session_id, terminal_error)
                await _mark_queued_generations_failed(
                    db_engine,
                    session_id=session_id,
                    error_message=str(exc),
                )
                break
    finally:
        with DBSession(db_engine) as worker_db_session:
            repository = SessionRepository(worker_db_session)
            session = repository.get_session(session_id)
            if session is not None:
                if terminal_error is not None:
                    session = repository.update_session(session, status=SessionStatus.ERROR)
                    await harness_generation_events.publish_session_updated(
                        event_broker,
                        session,
                        error=str(terminal_error),
                    )
                elif (
                    session.status == SessionStatus.RUNNING
                    and repository.get_active_generation(session.id) is None
                ):
                    session = repository.update_session(session, status=SessionStatus.DONE)
                    await harness_generation_events.publish_session_updated(
                        event_broker,
                        session,
                        queued_prompt_count=repository.queue_size(session.id),
                    )
        await generation_manager.worker_finished(session_id, current_task)


async def start_worker_if_needed(
    *,
    db_session: DBSession,
    session_id: str,
    event_broker: SessionEventBroker,
    generation_manager: SessionGenerationManager,
    chat_runtime: ChatRuntime,
    runtime_service: RuntimeService,
    skill_service: SkillService,
    mcp_service: MCPService,
) -> None:
    if not await generation_manager.should_start_worker(session_id):
        return
    db_engine = db_session.get_bind()
    if not isinstance(db_engine, Engine):
        raise RuntimeError("Database engine is unavailable.")
    worker_task = asyncio.create_task(
        run_session_worker(
            db_engine=db_engine,
            session_id=session_id,
            event_broker=event_broker,
            generation_manager=generation_manager,
            chat_runtime=chat_runtime,
            runtime_service=runtime_service,
            skill_service=skill_service,
            mcp_service=mcp_service,
        )
    )
    await generation_manager.attach_worker(session_id, worker_task)
