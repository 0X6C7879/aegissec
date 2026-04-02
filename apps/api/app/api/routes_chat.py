from __future__ import annotations

import asyncio
import re
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession

from app.compat.mcp.service import MCPService, get_mcp_service
from app.compat.skills.service import (
    SkillContentReadError,
    SkillLookupError,
    SkillService,
    get_skill_service,
)
from app.core.events import SessionEvent, SessionEventBroker, SessionEventType, get_event_broker
from app.db.models import (
    BranchForkRequest,
    ChatGenerationRead,
    ChatRequest,
    ChatResponse,
    GenerationAction,
    GenerationStatus,
    Message,
    MessageEditRequest,
    MessageKind,
    MessageMutationResponse,
    MessageRegenerateRequest,
    MessageRole,
    MessageRollbackRequest,
    MessageStatus,
    RuntimeExecuteRequest,
    RuntimePolicy,
    Session,
    SessionConversationRead,
    SessionStatus,
    attachments_from_storage,
    attachments_to_storage,
    to_chat_generation_read,
    to_conversation_branch_read,
    to_message_read,
    to_session_read,
    utc_now,
)
from app.db.repositories import SessionRepository
from app.db.session import get_db_session
from app.services.capabilities import CapabilityFacade
from app.services.chat_runtime import (
    ChatRuntime,
    ChatRuntimeConfigurationError,
    ChatRuntimeError,
    ConversationMessage,
    GenerationCallbacks,
    ToolCallRequest,
    ToolCallResult,
    get_chat_runtime,
    strip_think_blocks,
)
from app.services.runtime import (
    RuntimeArtifactPathError,
    RuntimeOperationError,
    RuntimePolicyViolationError,
    RuntimeService,
    get_runtime_service,
)
from app.services.session_generation import (
    GenerationCancelledError,
    SessionGenerationManager,
    get_generation_manager,
)

router = APIRouter(prefix="/api/sessions", tags=["chat"])

SAFE_THINKING_SUMMARY = "Assistant is analyzing the request and preparing a response."
THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN_TAG = "<think>"
_THINK_CLOSE_TAG = "</think>"


def _get_session_or_404(repository: SessionRepository, session_id: str) -> Session:
    session = repository.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


def _get_message_or_404(
    repository: SessionRepository,
    *,
    session_id: str,
    message_id: str,
) -> Message:
    message = repository.get_message(message_id)
    if message is None or message.session_id != session_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    return message


def _message_trace_entries(message: Message) -> list[dict[str, object]]:
    raw_trace = message.metadata_json.get("trace")
    if not isinstance(raw_trace, list):
        return []
    return [dict(entry) for entry in raw_trace if isinstance(entry, dict)]


def _persist_reasoning_trace_entry(
    repository: SessionRepository,
    *,
    assistant_message: Message,
    entry: dict[str, object],
) -> dict[str, object]:
    generation = None
    generation_trace_length = 0
    if assistant_message.generation_id is not None:
        generation = repository.get_generation(assistant_message.generation_id)
        if generation is not None:
            generation_trace_length = len(generation.reasoning_trace_json)

    message_trace_length = len(_message_trace_entries(assistant_message))
    persisted_entry = {
        "sequence": max(message_trace_length, generation_trace_length) + 1,
        "recorded_at": utc_now().isoformat(),
        **entry,
    }

    repository.append_message_trace(assistant_message, persisted_entry)
    if generation is not None:
        generation_trace = list(generation.reasoning_trace_json)
        generation_trace.append(dict(persisted_entry))
        repository.update_generation(generation, reasoning_trace_json=generation_trace)
    return persisted_entry


async def _publish_session_updated(
    event_broker: SessionEventBroker,
    session: Session,
    *,
    error: str | None = None,
    queued_prompt_count: int | None = None,
) -> None:
    payload: dict[str, Any] = to_session_read(session).model_dump(mode="json")
    if error is not None:
        payload["error"] = error
    if queued_prompt_count is not None:
        payload["queued_prompt_count"] = queued_prompt_count
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_UPDATED,
            session_id=session.id,
            payload=payload,
        )
    )


def _message_payload(message: Message) -> dict[str, Any]:
    payload = to_message_read(message).model_dump(mode="json")
    payload["message_id"] = payload["id"]
    return payload


async def _publish_message_event(
    event_broker: SessionEventBroker,
    *,
    event_type: SessionEventType,
    session_id: str,
    message: Message,
    delta: str | None = None,
) -> None:
    payload = _message_payload(message)
    if delta is not None:
        payload["delta"] = delta
    await event_broker.publish(
        SessionEvent(type=event_type, session_id=session_id, payload=payload)
    )


async def _publish_generation_started(
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    generation_id: str,
    user_message_id: str | None,
    assistant_message_id: str,
    queued_prompt_count: int,
) -> None:
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.GENERATION_STARTED,
            session_id=session_id,
            payload={
                "generation_id": generation_id,
                "user_message_id": user_message_id,
                "message_id": assistant_message_id,
                "queued_prompt_count": queued_prompt_count,
            },
        )
    )


async def _publish_generation_cancelled(
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    generation_id: str,
    assistant_message_id: str,
) -> None:
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.GENERATION_CANCELLED,
            session_id=session_id,
            payload={"generation_id": generation_id, "message_id": assistant_message_id},
        )
    )


async def _publish_generation_failed(
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    generation_id: str,
    assistant_message_id: str,
    error_message: str,
) -> None:
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.GENERATION_FAILED,
            session_id=session_id,
            payload={
                "generation_id": generation_id,
                "message_id": assistant_message_id,
                "error": error_message,
            },
        )
    )


async def _publish_assistant_summary(
    repository: SessionRepository,
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    assistant_message: Message,
    summary: str,
) -> None:
    sanitized_summary = strip_think_blocks(summary)
    if not sanitized_summary:
        sanitized_summary = SAFE_THINKING_SUMMARY
    repository.update_message_summary(assistant_message, sanitized_summary)
    if assistant_message.generation_id is not None:
        generation = repository.get_generation(assistant_message.generation_id)
        if generation is not None:
            repository.update_generation(generation, reasoning_summary=sanitized_summary)
    _persist_reasoning_trace_entry(
        repository,
        assistant_message=assistant_message,
        entry={
            "event": SessionEventType.ASSISTANT_SUMMARY.value,
            "state": "summary.updated",
            "summary": sanitized_summary,
        },
    )
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.ASSISTANT_SUMMARY,
            session_id=session_id,
            payload={"message_id": assistant_message.id, "summary": sanitized_summary},
        )
    )


async def _publish_assistant_trace(
    repository: SessionRepository,
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    assistant_message: Message,
    entry: dict[str, object],
) -> None:
    sanitized_entry: dict[str, object] = {}
    for key, value in entry.items():
        if isinstance(value, str):
            sanitized_value = strip_think_blocks(value)
            if THINK_BLOCK_RE.search(value) and not sanitized_value:
                continue
            sanitized_entry[key] = sanitized_value
        else:
            sanitized_entry[key] = value

    persisted_entry = _persist_reasoning_trace_entry(
        repository,
        assistant_message=assistant_message,
        entry={"event": SessionEventType.ASSISTANT_TRACE.value, **sanitized_entry},
    )
    payload = {"message_id": assistant_message.id, **persisted_entry}
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.ASSISTANT_TRACE,
            session_id=session_id,
            payload=payload,
        )
    )


def _project_visible_stream_content(content: str) -> str:
    visible_parts: list[str] = []
    index = 0
    inside_think_block = False

    while index < len(content):
        remaining_content = content[index:]
        remaining_lower = remaining_content.lower()

        if inside_think_block:
            if remaining_lower.startswith(_THINK_CLOSE_TAG):
                inside_think_block = False
                index += len(_THINK_CLOSE_TAG)
                continue
            if _THINK_CLOSE_TAG.startswith(remaining_lower):
                break
            index += 1
            continue

        if remaining_lower.startswith(_THINK_OPEN_TAG):
            inside_think_block = True
            index += len(_THINK_OPEN_TAG)
            continue
        if _THINK_OPEN_TAG.startswith(remaining_lower) or _THINK_CLOSE_TAG.startswith(
            remaining_lower
        ):
            break

        visible_parts.append(content[index])
        index += 1

    return "".join(visible_parts).strip()


def _build_conversation_messages(messages: list[Message]) -> list[ConversationMessage]:
    conversation_messages: list[ConversationMessage] = []
    for message in messages:
        if message.message_kind != MessageKind.MESSAGE:
            continue
        if message.role not in {MessageRole.USER, MessageRole.ASSISTANT}:
            continue
        if message.role == MessageRole.ASSISTANT and not message.content.strip():
            continue
        conversation_messages.append(
            ConversationMessage(
                role=message.role,
                content=message.content,
                attachments=attachments_from_storage(message.attachments_json),
            )
        )
    return conversation_messages


def _build_conversation_read(
    repository: SessionRepository,
    session: Session,
) -> SessionConversationRead:
    active_branch = repository.ensure_active_branch(session)
    branches = repository.list_branches(session.id)
    messages = repository.list_messages(
        session.id, branch_id=active_branch.id, include_superseded=False
    )
    return SessionConversationRead(
        session=to_session_read(session),
        active_branch=to_conversation_branch_read(active_branch),
        branches=[to_conversation_branch_read(branch) for branch in branches],
        messages=[to_message_read(message) for message in messages],
        generations=[
            to_chat_generation_read(generation)
            for generation in repository.list_generations(session.id)
            if generation.branch_id == active_branch.id
        ],
    )


def _find_sibling_version_group_id(
    repository: SessionRepository,
    *,
    session_id: str,
    branch_id: str | None,
    sequence: int,
    role: MessageRole,
) -> str | None:
    for message in repository.list_all_messages(session_id):
        if (
            message.branch_id == branch_id
            and message.sequence == sequence
            and message.role == role
            and message.version_group_id is not None
        ):
            return message.version_group_id
    return None


def _build_tool_executor(
    *,
    session: Session,
    assistant_message: Message,
    repository: SessionRepository,
    event_broker: SessionEventBroker,
    runtime_service: RuntimeService,
    skill_service: SkillService,
    mcp_service: MCPService,
) -> Any:
    available_skills = skill_service.list_loaded_skills_for_agent()
    del mcp_service

    async def execute_tool(tool_request: ToolCallRequest) -> ToolCallResult:
        started_payload: dict[str, Any] = {
            "tool": tool_request.tool_name,
            "tool_call_id": tool_request.tool_call_id,
            "arguments": tool_request.arguments,
        }
        if tool_request.tool_name == "execute_kali_command":
            started_payload.update(
                {
                    "command": tool_request.arguments.get("command"),
                    "timeout_seconds": tool_request.arguments.get("timeout_seconds"),
                    "artifact_paths": tool_request.arguments.get("artifact_paths", []),
                }
            )

        await _publish_assistant_trace(
            repository,
            event_broker,
            session_id=session.id,
            assistant_message=assistant_message,
            entry={
                "state": "tool.started",
                "tool": tool_request.tool_name,
                "tool_call_id": tool_request.tool_call_id,
            },
        )
        await event_broker.publish(
            SessionEvent(
                type=SessionEventType.TOOL_CALL_STARTED,
                session_id=session.id,
                payload=started_payload,
            )
        )

        async def publish_tool_failed(error_message: str) -> None:
            await _publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=assistant_message,
                entry={
                    "state": "tool.failed",
                    "tool": tool_request.tool_name,
                    "tool_call_id": tool_request.tool_call_id,
                    "error": error_message,
                },
            )
            await event_broker.publish(
                SessionEvent(
                    type=SessionEventType.TOOL_CALL_FAILED,
                    session_id=session.id,
                    payload={**started_payload, "error": error_message},
                )
            )

        if tool_request.tool_name == "execute_kali_command":
            command = tool_request.arguments.get("command")
            timeout_seconds = tool_request.arguments.get("timeout_seconds")
            artifact_paths = tool_request.arguments.get("artifact_paths", [])
            if not isinstance(command, str) or not command.strip():
                await publish_tool_failed("Invalid command for execute_kali_command.")
                raise ChatRuntimeError("Invalid command for execute_kali_command.")
            if timeout_seconds is not None and not isinstance(timeout_seconds, int):
                await publish_tool_failed("Invalid timeout for execute_kali_command.")
                raise ChatRuntimeError("Invalid timeout for execute_kali_command.")
            if not isinstance(artifact_paths, list) or not all(
                isinstance(item, str) for item in artifact_paths
            ):
                await publish_tool_failed("Invalid artifact paths for execute_kali_command.")
                raise ChatRuntimeError("Invalid artifact paths for execute_kali_command.")

            try:
                run = runtime_service.execute(
                    RuntimeExecuteRequest(
                        command=command,
                        timeout_seconds=timeout_seconds,
                        session_id=session.id,
                        artifact_paths=artifact_paths,
                    ),
                    runtime_policy=RuntimePolicy.model_validate(session.runtime_policy_json or {}),
                )
            except ValidationError as exc:
                await publish_tool_failed(f"Invalid runtime policy: {exc.errors()[0]['msg']}")
                raise ChatRuntimeError("Invalid runtime policy for this session.") from exc
            except (
                RuntimeArtifactPathError,
                RuntimeOperationError,
                RuntimePolicyViolationError,
            ) as exc:
                await publish_tool_failed(str(exc))
                raise ChatRuntimeError(str(exc)) from exc

            command_result_payload: dict[str, Any] = {
                "status": run.status.value,
                "exit_code": run.exit_code,
                "stdout": run.stdout,
                "stderr": run.stderr,
                "artifacts": [artifact.relative_path for artifact in run.artifacts],
            }
            await _publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=assistant_message,
                entry={
                    "state": "tool.finished",
                    "tool": tool_request.tool_name,
                    "tool_call_id": tool_request.tool_call_id,
                    "status": run.status.value,
                },
            )
            await event_broker.publish(
                SessionEvent(
                    type=SessionEventType.TOOL_CALL_FINISHED,
                    session_id=session.id,
                    payload={
                        **started_payload,
                        "tool": tool_request.tool_name,
                        "tool_call_id": tool_request.tool_call_id,
                        "run_id": run.id,
                        "command": run.command,
                        "status": run.status.value,
                        "exit_code": run.exit_code,
                        "requested_timeout_seconds": run.requested_timeout_seconds,
                        "stdout": run.stdout,
                        "stderr": run.stderr,
                        "created_at": run.created_at.isoformat(),
                        "artifact_paths": [artifact.relative_path for artifact in run.artifacts],
                        "result": command_result_payload,
                    },
                )
            )
            return ToolCallResult(tool_name=tool_request.tool_name, payload=command_result_payload)

        if tool_request.tool_name == "list_available_skills":
            skills_result_payload: dict[str, Any] = {
                "skills": [skill.model_dump(mode="json") for skill in available_skills],
            }
            await _publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=assistant_message,
                entry={
                    "state": "tool.finished",
                    "tool": tool_request.tool_name,
                    "tool_call_id": tool_request.tool_call_id,
                },
            )
            await event_broker.publish(
                SessionEvent(
                    type=SessionEventType.TOOL_CALL_FINISHED,
                    session_id=session.id,
                    payload={**started_payload, "result": skills_result_payload},
                )
            )
            return ToolCallResult(tool_name=tool_request.tool_name, payload=skills_result_payload)

        if tool_request.tool_name == "read_skill_content":
            skill_name_or_id = tool_request.arguments.get("skill_name_or_id")
            if not isinstance(skill_name_or_id, str) or not skill_name_or_id.strip():
                await publish_tool_failed("read_skill_content requires a valid skill identifier.")
                raise ChatRuntimeError("read_skill_content requires a valid skill identifier.")

            try:
                skill_content = skill_service.read_skill_content_by_name_or_directory_name(
                    skill_name_or_id
                )
            except (SkillLookupError, SkillContentReadError) as exc:
                await publish_tool_failed(str(exc))
                raise ChatRuntimeError(str(exc)) from exc

            skill_result_payload: dict[str, Any] = {"skill": skill_content.model_dump(mode="json")}
            await _publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=assistant_message,
                entry={
                    "state": "tool.finished",
                    "tool": tool_request.tool_name,
                    "tool_call_id": tool_request.tool_call_id,
                },
            )
            await event_broker.publish(
                SessionEvent(
                    type=SessionEventType.TOOL_CALL_FINISHED,
                    session_id=session.id,
                    payload={**started_payload, "result": skill_result_payload},
                )
            )
            return ToolCallResult(tool_name=tool_request.tool_name, payload=skill_result_payload)

        error_message = f"Unsupported tool requested: {tool_request.tool_name}."
        await publish_tool_failed(error_message)
        raise ChatRuntimeError(error_message)

    return execute_tool


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
            repository.mark_generation_failed(generation, error_message)
            assistant_message = repository.get_message(generation.assistant_message_id)
            if assistant_message is not None:
                repository.update_message(
                    assistant_message,
                    status=MessageStatus.FAILED,
                    error_message=error_message,
                )


async def _process_generation(
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
            session = _get_session_or_404(repository, session_id)
            generation = repository.get_generation(generation_id)
            assistant_message = repository.get_message(initial_assistant_message.id)
            if generation is None or assistant_message is None:
                raise ChatRuntimeError("Generation state could not be reloaded.")
            loaded_assistant_message = assistant_message
            user_message = (
                repository.get_message(generation.user_message_id)
                if generation.user_message_id is not None
                else None
            )
            token_budget = generation.metadata_json.get("token_budget")

            available_skills = skill_service.list_loaded_skills_for_agent()
            capability_facade = CapabilityFacade(
                skill_service=skill_service,
                mcp_service=mcp_service,
            )
            skill_context_prompt = capability_facade.build_skill_prompt_fragment()
            execute_tool = _build_tool_executor(
                session=session,
                assistant_message=loaded_assistant_message,
                repository=repository,
                event_broker=event_broker,
                runtime_service=runtime_service,
                skill_service=skill_service,
                mcp_service=mcp_service,
            )
            conversation_history = repository.build_conversation_context(
                session_id=session.id,
                branch_id=generation.branch_id,
                rough_token_budget=token_budget if isinstance(token_budget, int) else 12_000,
            )

            await _publish_generation_started(
                event_broker,
                session_id=session.id,
                generation_id=generation.id,
                user_message_id=generation.user_message_id,
                assistant_message_id=assistant_message.id,
                queued_prompt_count=repository.queue_size(session.id),
            )
            await _publish_assistant_summary(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=assistant_message,
                summary=SAFE_THINKING_SUMMARY,
            )
            await _publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=assistant_message,
                entry={"state": "generation.started", "generation_id": generation.id},
            )

            raw_streamed_content = loaded_assistant_message.content
            streamed_content = _project_visible_stream_content(raw_streamed_content)

            async def on_text_delta(delta: str) -> None:
                nonlocal raw_streamed_content, streamed_content
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                raw_streamed_content += delta
                next_streamed_content = _project_visible_stream_content(raw_streamed_content)
                if next_streamed_content == streamed_content:
                    return

                sanitized_delta = (
                    next_streamed_content[len(streamed_content) :]
                    if next_streamed_content.startswith(streamed_content)
                    else next_streamed_content
                )
                streamed_content = next_streamed_content
                repository.update_message(
                    loaded_assistant_message,
                    content=streamed_content,
                    status=MessageStatus.STREAMING,
                )
                await _publish_message_event(
                    event_broker,
                    event_type=SessionEventType.MESSAGE_DELTA,
                    session_id=session.id,
                    message=loaded_assistant_message,
                    delta=sanitized_delta,
                )
                await _publish_message_event(
                    event_broker,
                    event_type=SessionEventType.MESSAGE_UPDATED,
                    session_id=session.id,
                    message=loaded_assistant_message,
                )

            async def on_summary(summary: str) -> None:
                if summary.strip():
                    await _publish_assistant_summary(
                        repository,
                        event_broker,
                        session_id=session.id,
                        assistant_message=loaded_assistant_message,
                        summary=summary.strip(),
                    )

            final_content = await chat_runtime.generate_reply(
                (
                    user_message.content
                    if user_message is not None
                    else loaded_assistant_message.content
                ),
                (
                    attachments_from_storage(user_message.attachments_json)
                    if user_message is not None
                    else []
                ),
                conversation_messages=_build_conversation_messages(conversation_history),
                available_skills=available_skills,
                skill_context_prompt=skill_context_prompt,
                execute_tool=execute_tool,
                callbacks=GenerationCallbacks(
                    on_text_delta=on_text_delta,
                    on_summary=on_summary,
                    is_cancelled=cancel_event.is_set,
                ),
            )
            final_content = strip_think_blocks(final_content) or (
                "模型已完成分析，但没有返回可展示的最终答复。"
            )

            if cancel_event.is_set():
                raise asyncio.CancelledError

            repository.update_message(
                loaded_assistant_message,
                content=final_content,
                status=MessageStatus.COMPLETED,
                error_message="",
            )
            repository.mark_generation_completed(generation)
            if final_content != streamed_content:
                delta = (
                    final_content[len(streamed_content) :]
                    if final_content.startswith(streamed_content)
                    else final_content
                )
                if delta:
                    await _publish_message_event(
                        event_broker,
                        event_type=SessionEventType.MESSAGE_DELTA,
                        session_id=session.id,
                        message=loaded_assistant_message,
                        delta=delta,
                    )
                await _publish_message_event(
                    event_broker,
                    event_type=SessionEventType.MESSAGE_UPDATED,
                    session_id=session.id,
                    message=loaded_assistant_message,
                )
            await _publish_message_event(
                event_broker,
                event_type=SessionEventType.MESSAGE_COMPLETED,
                session_id=session.id,
                message=loaded_assistant_message,
            )
            await _publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=loaded_assistant_message,
                entry={"state": "generation.completed", "generation_id": generation.id},
            )
            return loaded_assistant_message.id
    except asyncio.CancelledError as exc:
        with DBSession(db_engine) as worker_db_session:
            repository = SessionRepository(worker_db_session)
            generation = repository.get_generation(generation_id)
            assistant_message = None
            if generation is not None:
                repository.cancel_generation(
                    generation, error_message="Active generation was cancelled."
                )
                assistant_message = repository.get_message(generation.assistant_message_id)
            if assistant_message is not None:
                repository.update_message(
                    assistant_message,
                    status=MessageStatus.CANCELLED,
                    error_message="Active generation was cancelled.",
                )
                await _publish_assistant_trace(
                    repository,
                    event_broker,
                    session_id=session_id,
                    assistant_message=assistant_message,
                    entry={"state": "generation.cancelled", "generation_id": generation_id},
                )
                await _publish_generation_cancelled(
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
                repository.mark_generation_failed(generation, str(exc))
                assistant_message = repository.get_message(generation.assistant_message_id)
            if assistant_message is not None:
                repository.update_message(
                    assistant_message,
                    status=MessageStatus.FAILED,
                    error_message=str(exc),
                )
                await _publish_assistant_trace(
                    repository,
                    event_broker,
                    session_id=session_id,
                    assistant_message=assistant_message,
                    entry={
                        "state": "generation.failed",
                        "generation_id": generation_id,
                        "error": str(exc),
                    },
                )
                await _publish_generation_failed(
                    event_broker,
                    session_id=session_id,
                    generation_id=generation_id,
                    assistant_message_id=assistant_message.id,
                    error_message=str(exc),
                )
        raise
    finally:
        await generation_manager.clear_current_generation(session_id, generation_id)


async def _run_session_worker(
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
                assistant_message_id = await _process_generation(
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
                    session_id, generation.id, assistant_message_id
                )
            except GenerationCancelledError as exc:
                await generation_manager.reject_future(session_id, generation.id, exc)
                continue
            except ChatRuntimeConfigurationError as exc:
                terminal_error = exc
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
                await generation_manager.reject_future(session_id, generation.id, exc)
                await generation_manager.reject_pending(session_id, exc)
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
                    await _publish_session_updated(
                        event_broker,
                        session,
                        error=str(terminal_error),
                    )
                elif (
                    session.status == SessionStatus.RUNNING
                    and repository.get_active_generation(session.id) is None
                ):
                    session = repository.update_session(session, status=SessionStatus.DONE)
                    await _publish_session_updated(
                        event_broker,
                        session,
                        queued_prompt_count=repository.queue_size(session.id),
                    )
        await generation_manager.worker_finished(session_id, current_task)


async def _start_worker_if_needed(
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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database engine is unavailable.",
        )
    worker_task = asyncio.create_task(
        _run_session_worker(
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


async def _await_generation_result(
    *,
    session_id: str,
    generation_id: str,
    future: asyncio.Future[str],
) -> str:
    del session_id
    try:
        return await future
    except GenerationCancelledError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ChatRuntimeConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ChatRuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


def _ensure_running_session(repository: SessionRepository, session: Session) -> Session | None:
    if session.status == SessionStatus.RUNNING:
        return None
    return repository.update_session(session, status=SessionStatus.RUNNING)


@router.post("/{session_id}/chat", response_model=ChatResponse)
async def create_chat_message(
    session_id: str,
    payload: ChatRequest,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    chat_runtime: ChatRuntime = Depends(get_chat_runtime),
    runtime_service: RuntimeService = Depends(get_runtime_service),
    skill_service: SkillService = Depends(get_skill_service),
    mcp_service: MCPService = Depends(get_mcp_service),
    generation_manager: SessionGenerationManager = Depends(get_generation_manager),
) -> ChatResponse:
    repository = SessionRepository(db_session)
    session = _get_session_or_404(repository, session_id)
    if payload.branch_id is not None:
        branch = repository.get_branch(payload.branch_id)
        if branch is None or branch.session_id != session.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")
        session = repository.activate_branch(session, branch)
    else:
        branch = repository.ensure_active_branch(session)

    running_session = _ensure_running_session(repository, session)
    if running_session is not None:
        session = running_session
        await _publish_session_updated(event_broker, session)

    next_sequence, next_turn_index = repository.get_next_message_slot(branch.id)
    parent_message = (
        repository.get_message(payload.parent_message_id)
        if payload.parent_message_id is not None
        else repository.get_latest_visible_message(branch.id)
    )
    if parent_message is not None and parent_message.session_id != session.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Parent message not found"
        )
    user_message = repository.create_message(
        session=session,
        role=MessageRole.USER,
        content=payload.content,
        attachments=attachments_to_storage(payload.attachments),
        parent_message_id=parent_message.id if parent_message is not None else None,
        branch_id=branch.id,
        status=MessageStatus.COMPLETED,
        message_kind=MessageKind.MESSAGE,
        sequence=next_sequence,
        turn_index=next_turn_index,
    )
    assistant_message = repository.create_message(
        session=session,
        role=MessageRole.ASSISTANT,
        content="",
        attachments=[],
        parent_message_id=user_message.id,
        branch_id=branch.id,
        status=MessageStatus.PENDING,
        message_kind=MessageKind.MESSAGE,
        sequence=next_sequence + 1,
        turn_index=next_turn_index,
    )
    generation = repository.create_generation(
        session_id=session.id,
        branch_id=branch.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        action=GenerationAction.REPLY,
        metadata_json={
            "operation": "chat",
            "token_budget": payload.token_budget,
            "parent_message_id": payload.parent_message_id,
        },
    )
    repository.update_message(assistant_message, generation_id=generation.id)

    await _publish_message_event(
        event_broker,
        event_type=SessionEventType.MESSAGE_CREATED,
        session_id=session.id,
        message=user_message,
    )
    await _publish_message_event(
        event_broker,
        event_type=SessionEventType.MESSAGE_CREATED,
        session_id=session.id,
        message=assistant_message,
    )

    future = await generation_manager.register_future(session.id, generation.id)
    queued_prompt_count = repository.queue_size(session.id)
    should_start_worker = await generation_manager.should_start_worker(session.id)
    if not should_start_worker:
        await _publish_session_updated(
            event_broker,
            session,
            queued_prompt_count=max(1, queued_prompt_count),
        )
    await _start_worker_if_needed(
        db_session=db_session,
        session_id=session.id,
        event_broker=event_broker,
        generation_manager=generation_manager,
        chat_runtime=chat_runtime,
        runtime_service=runtime_service,
        skill_service=skill_service,
        mcp_service=mcp_service,
    )

    await _await_generation_result(
        session_id=session.id, generation_id=generation.id, future=future
    )
    db_session.expire_all()
    refreshed_session = _get_session_or_404(repository, session_id)
    refreshed_assistant_message = repository.get_message(assistant_message.id)
    if refreshed_assistant_message is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Assistant message was not persisted.",
        )
    return ChatResponse(
        session=to_session_read(refreshed_session),
        user_message=to_message_read(user_message),
        assistant_message=to_message_read(refreshed_assistant_message),
        generation=to_chat_generation_read(generation),
        branch=to_conversation_branch_read(branch),
    )


@router.post("/{session_id}/messages/{message_id}/edit", response_model=MessageMutationResponse)
async def edit_message(
    session_id: str,
    message_id: str,
    payload: MessageEditRequest,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    chat_runtime: ChatRuntime = Depends(get_chat_runtime),
    runtime_service: RuntimeService = Depends(get_runtime_service),
    skill_service: SkillService = Depends(get_skill_service),
    mcp_service: MCPService = Depends(get_mcp_service),
    generation_manager: SessionGenerationManager = Depends(get_generation_manager),
) -> MessageMutationResponse:
    repository = SessionRepository(db_session)
    session = _get_session_or_404(repository, session_id)
    target_message = _get_message_or_404(repository, session_id=session_id, message_id=message_id)
    if target_message.role != MessageRole.USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Only user messages can be edited."
        )
    branch_id = payload.branch_id or target_message.branch_id
    branch = repository.get_branch(branch_id or "")
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Message branch was not found."
        )

    running_session = _ensure_running_session(repository, session)
    if running_session is not None:
        session = running_session
        await _publish_session_updated(event_broker, session)

    repository.supersede_branch_descendants(
        branch_id=branch.id,
        sequence=target_message.sequence,
        inclusive=True,
    )
    edited_message = repository.create_message(
        session=session,
        role=MessageRole.USER,
        content=payload.content,
        attachments=attachments_to_storage(payload.attachments),
        parent_message_id=target_message.parent_message_id,
        branch_id=branch.id,
        status=MessageStatus.COMPLETED,
        message_kind=MessageKind.MESSAGE,
        sequence=target_message.sequence,
        turn_index=target_message.turn_index,
        edited_from_message_id=target_message.id,
        version_group_id=target_message.version_group_id or target_message.id,
    )
    assistant_group_id = _find_sibling_version_group_id(
        repository,
        session_id=session.id,
        branch_id=branch.id,
        sequence=target_message.sequence + 1,
        role=MessageRole.ASSISTANT,
    )
    assistant_message = repository.create_message(
        session=session,
        role=MessageRole.ASSISTANT,
        content="",
        attachments=[],
        parent_message_id=edited_message.id,
        branch_id=branch.id,
        status=MessageStatus.PENDING,
        message_kind=MessageKind.MESSAGE,
        sequence=target_message.sequence + 1,
        turn_index=target_message.turn_index,
        version_group_id=assistant_group_id,
    )
    generation = repository.create_generation(
        session_id=session.id,
        branch_id=branch.id,
        user_message_id=edited_message.id,
        assistant_message_id=assistant_message.id,
        action=GenerationAction.EDIT,
        target_message_id=target_message.id,
        metadata_json={
            "operation": "edit",
            "edited_message_id": target_message.id,
            "token_budget": payload.token_budget,
        },
    )
    repository.update_message(assistant_message, generation_id=generation.id)
    session = repository.activate_branch(session, branch)

    await _publish_message_event(
        event_broker,
        event_type=SessionEventType.MESSAGE_CREATED,
        session_id=session.id,
        message=edited_message,
    )
    await _publish_message_event(
        event_broker,
        event_type=SessionEventType.MESSAGE_CREATED,
        session_id=session.id,
        message=assistant_message,
    )

    future = await generation_manager.register_future(session.id, generation.id)
    await _start_worker_if_needed(
        db_session=db_session,
        session_id=session.id,
        event_broker=event_broker,
        generation_manager=generation_manager,
        chat_runtime=chat_runtime,
        runtime_service=runtime_service,
        skill_service=skill_service,
        mcp_service=mcp_service,
    )
    await _await_generation_result(
        session_id=session.id, generation_id=generation.id, future=future
    )
    db_session.expire_all()
    refreshed_session = _get_session_or_404(repository, session_id)
    refreshed_branch = repository.get_branch(branch.id)
    refreshed_assistant = repository.get_message(assistant_message.id)
    refreshed_generation = repository.get_generation(generation.id)
    if refreshed_branch is None or refreshed_assistant is None or refreshed_generation is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Mutation state was not persisted.",
        )
    return MessageMutationResponse(
        session=to_session_read(refreshed_session),
        branch=to_conversation_branch_read(refreshed_branch),
        user_message=to_message_read(edited_message),
        assistant_message=to_message_read(refreshed_assistant),
        generation=to_chat_generation_read(refreshed_generation),
    )


@router.post(
    "/{session_id}/messages/{message_id}/regenerate",
    response_model=MessageMutationResponse,
)
async def regenerate_message(
    session_id: str,
    message_id: str,
    payload: MessageRegenerateRequest | None = None,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    chat_runtime: ChatRuntime = Depends(get_chat_runtime),
    runtime_service: RuntimeService = Depends(get_runtime_service),
    skill_service: SkillService = Depends(get_skill_service),
    mcp_service: MCPService = Depends(get_mcp_service),
    generation_manager: SessionGenerationManager = Depends(get_generation_manager),
) -> MessageMutationResponse:
    repository = SessionRepository(db_session)
    session = _get_session_or_404(repository, session_id)
    target_message = _get_message_or_404(repository, session_id=session_id, message_id=message_id)
    if target_message.role != MessageRole.ASSISTANT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only assistant messages can be regenerated.",
        )
    branch_id = (
        payload.branch_id
        if payload is not None and payload.branch_id is not None
        else target_message.branch_id
    )
    branch = repository.get_branch(branch_id or "")
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Message branch was not found."
        )

    running_session = _ensure_running_session(repository, session)
    if running_session is not None:
        session = running_session
        await _publish_session_updated(event_broker, session)

    repository.supersede_branch_descendants(
        branch_id=branch.id,
        sequence=target_message.sequence,
        inclusive=True,
    )
    assistant_message = repository.create_message(
        session=session,
        role=MessageRole.ASSISTANT,
        content="",
        attachments=[],
        parent_message_id=target_message.parent_message_id,
        branch_id=branch.id,
        status=MessageStatus.PENDING,
        message_kind=MessageKind.MESSAGE,
        sequence=target_message.sequence,
        turn_index=target_message.turn_index,
        version_group_id=target_message.version_group_id or target_message.id,
    )
    generation = repository.create_generation(
        session_id=session.id,
        branch_id=branch.id,
        user_message_id=target_message.parent_message_id,
        assistant_message_id=assistant_message.id,
        action=GenerationAction.REGENERATE,
        target_message_id=target_message.id,
        metadata_json={
            "operation": "regenerate",
            "regenerated_message_id": target_message.id,
            "token_budget": payload.token_budget if payload is not None else None,
        },
    )
    repository.update_message(assistant_message, generation_id=generation.id)
    session = repository.activate_branch(session, branch)

    await _publish_message_event(
        event_broker,
        event_type=SessionEventType.MESSAGE_CREATED,
        session_id=session.id,
        message=assistant_message,
    )

    future = await generation_manager.register_future(session.id, generation.id)
    await _start_worker_if_needed(
        db_session=db_session,
        session_id=session.id,
        event_broker=event_broker,
        generation_manager=generation_manager,
        chat_runtime=chat_runtime,
        runtime_service=runtime_service,
        skill_service=skill_service,
        mcp_service=mcp_service,
    )
    await _await_generation_result(
        session_id=session.id, generation_id=generation.id, future=future
    )
    db_session.expire_all()
    refreshed_session = _get_session_or_404(repository, session_id)
    refreshed_branch = repository.get_branch(branch.id)
    refreshed_assistant = repository.get_message(assistant_message.id)
    refreshed_generation = repository.get_generation(generation.id)
    if refreshed_branch is None or refreshed_assistant is None or refreshed_generation is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Mutation state was not persisted.",
        )
    return MessageMutationResponse(
        session=to_session_read(refreshed_session),
        branch=to_conversation_branch_read(refreshed_branch),
        assistant_message=to_message_read(refreshed_assistant),
        generation=to_chat_generation_read(refreshed_generation),
    )


@router.post("/{session_id}/messages/{message_id}/fork", response_model=SessionConversationRead)
async def fork_from_message(
    session_id: str,
    message_id: str,
    payload: BranchForkRequest | None = None,
    db_session: DBSession = Depends(get_db_session),
) -> SessionConversationRead:
    repository = SessionRepository(db_session)
    session = _get_session_or_404(repository, session_id)
    target_message = _get_message_or_404(repository, session_id=session_id, message_id=message_id)
    source_branch = repository.get_branch(target_message.branch_id or "")
    if source_branch is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Message branch was not found."
        )
    new_branch = repository.create_branch(
        session=session,
        parent_branch_id=source_branch.id,
        forked_from_message_id=target_message.id,
        name=payload.name if payload is not None else None,
    )
    repository.clone_branch_path_to_message(
        session=session,
        source_branch_id=source_branch.id,
        target_message=target_message,
        new_branch=new_branch,
    )
    session = repository.activate_branch(session, new_branch)
    db_session.expire_all()
    refreshed_session = _get_session_or_404(repository, session_id)
    return _build_conversation_read(repository, refreshed_session)


@router.post("/{session_id}/messages/{message_id}/rollback", response_model=SessionConversationRead)
async def rollback_to_message(
    session_id: str,
    message_id: str,
    payload: MessageRollbackRequest | None = None,
    db_session: DBSession = Depends(get_db_session),
) -> SessionConversationRead:
    repository = SessionRepository(db_session)
    session = _get_session_or_404(repository, session_id)
    target_message = _get_message_or_404(repository, session_id=session_id, message_id=message_id)
    branch_id = (
        payload.branch_id
        if payload is not None and payload.branch_id is not None
        else target_message.branch_id
    )
    branch = repository.get_branch(branch_id or "")
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Message branch was not found."
        )
    repository.supersede_branch_descendants(
        branch_id=branch.id,
        sequence=target_message.sequence,
        inclusive=False,
    )
    session = repository.activate_branch(session, branch)
    db_session.expire_all()
    refreshed_session = _get_session_or_404(repository, session_id)
    return _build_conversation_read(repository, refreshed_session)


@router.post(
    "/{session_id}/generations/{generation_id}/cancel",
    response_model=ChatGenerationRead,
)
async def cancel_generation(
    session_id: str,
    generation_id: str,
    db_session: DBSession = Depends(get_db_session),
    generation_manager: SessionGenerationManager = Depends(get_generation_manager),
) -> ChatGenerationRead:
    repository = SessionRepository(db_session)
    _get_session_or_404(repository, session_id)
    generation = repository.get_generation(generation_id)
    if generation is None or generation.session_id != session_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Generation not found")

    assistant_message = repository.get_message(generation.assistant_message_id)
    if generation.status == GenerationStatus.QUEUED:
        repository.cancel_generation(generation, error_message="Queued generation was cancelled.")
        if assistant_message is not None:
            repository.update_message(
                assistant_message,
                status=MessageStatus.CANCELLED,
                error_message="Queued generation was cancelled.",
            )
        await generation_manager.reject_future(
            session_id,
            generation.id,
            GenerationCancelledError("Queued generation was cancelled."),
        )
    elif generation.status == GenerationStatus.RUNNING:
        repository.cancel_generation(generation, error_message="Active generation was cancelled.")
        if assistant_message is not None:
            repository.update_message(
                assistant_message,
                status=MessageStatus.CANCELLED,
                error_message="Active generation was cancelled.",
            )
        await generation_manager.cancel_generation(session_id, generation.id)
    refreshed_generation = repository.get_generation(generation.id)
    if refreshed_generation is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Generation state was not persisted.",
        )
    return to_chat_generation_read(refreshed_generation)
