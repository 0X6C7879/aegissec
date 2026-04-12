from __future__ import annotations

import asyncio
import importlib
import re
from collections.abc import Mapping

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session as DBSession

from app.compat.mcp.service import (
    MCPService,
    get_mcp_service,
)
from app.compat.skills.governance_discovery import stable_governance_skill_id
from app.compat.skills.service import (
    SkillService,
    get_skill_service,
)
from app.core.events import SessionEventBroker, SessionEventType, get_event_broker
from app.db.models import (
    BranchForkRequest,
    ChatGeneration,
    ChatGenerationRead,
    ChatRequest,
    ChatResponse,
    GenerationAction,
    GenerationStatus,
    GenerationStepRead,
    Message,
    MessageEditRequest,
    MessageKind,
    MessageMutationResponse,
    MessageRegenerateRequest,
    MessageRole,
    MessageRollbackRequest,
    MessageStatus,
    Session,
    SessionConversationRead,
    SessionStatus,
    SkillAgentSummaryRead,
    SlashActionInvocation,
    SlashActionSelection,
    SlashCatalogItem,
    attachments_to_storage,
    to_chat_generation_read,
    to_conversation_branch_read,
    to_generation_step_read,
    to_message_read,
    to_session_read,
    utc_now,
)
from app.db.repositories import SessionRepository
from app.db.session import get_db_session
from app.harness.continuations import (
    clear_generation_continuation_state as harness_clear_generation_continuation_state,
)
from app.services.capabilities import CapabilityFacade
from app.services.chat_runtime import (
    ChatRuntime,
    ChatRuntimeConfigurationError,
    ChatRuntimeError,
    get_chat_runtime,
)
from app.services.runtime import (
    RuntimeService,
    get_runtime_service,
)
from app.services.session_generation import (
    GenerationCancelledError,
    GenerationPausedError,
    SessionGenerationManager,
    get_generation_manager,
)

router = APIRouter(prefix="/api/sessions", tags=["chat"])

_harness_generation_events = importlib.import_module("app.harness.generation_events")
_harness_semantic = importlib.import_module("app.harness.semantic")
_harness_session_runner = importlib.import_module("app.harness.session_runner")
_harness_tool_runtime_runner = importlib.import_module("app.harness.tool_runtime_runner")
_harness_trace = importlib.import_module("app.harness.trace")
_harness_transcript = importlib.import_module("app.harness.transcript")

ToolRuntimeLifecycleRunner = _harness_tool_runtime_runner.ToolRuntimeLifecycleRunner
start_session_worker_if_needed = _harness_session_runner.start_worker_if_needed
_publish_attack_graph_updated = _harness_generation_events.publish_attack_graph_updated
_publish_generation_cancelled = _harness_generation_events.publish_generation_cancelled
_publish_generation_failed = _harness_generation_events.publish_generation_failed
_publish_generation_started = _harness_generation_events.publish_generation_started
_publish_message_event = _harness_generation_events.publish_message_event
_publish_session_updated = _harness_generation_events.publish_session_updated
_drain_semantic_snapshot = _harness_semantic.drain_semantic_snapshot
_semantic_snapshot_from_state = _harness_semantic.semantic_snapshot_from_state
_stage_semantic_deltas = _harness_semantic.stage_semantic_deltas
_stage_swarm_notification_semantics = _harness_semantic.stage_swarm_notification_semantics
_get_or_create_output_step = _harness_trace.get_or_create_output_step
_infer_trace_phase = _harness_trace.infer_trace_phase
_infer_trace_status = _harness_trace.infer_trace_status
_infer_trace_summary = _harness_trace.infer_trace_summary
_message_trace_entries = _harness_trace.message_trace_entries
_persist_reasoning_trace_entry = _harness_trace.persist_reasoning_trace_entry
_record_generation_step = _harness_trace.record_generation_step
_append_output_transcript_delta = _harness_transcript.append_output_transcript_delta
_append_transcript_segment = _harness_transcript.append_transcript_segment
_find_transcript_segment = _harness_transcript.find_transcript_segment
_latest_transcript_segment = _harness_transcript.latest_transcript_segment
_message_transcript_segments = _harness_transcript.message_transcript_segments
_update_transcript_segment = _harness_transcript.update_transcript_segment


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


def _clear_generation_continuation_state(
    repository: SessionRepository,
    generation: ChatGeneration,
    assistant_message: Message | None,
    *,
    abort_reason: str | None = None,
) -> None:
    harness_clear_generation_continuation_state(
        repository,
        generation,
        assistant_message,
        abort_reason=abort_reason,
    )


def _build_conversation_read(
    repository: SessionRepository,
    session: Session,
) -> SessionConversationRead:
    active_branch = repository.ensure_active_branch(session)
    branches = repository.list_branches(session.id)
    messages = repository.list_messages(
        session.id, branch_id=active_branch.id, include_superseded=False
    )
    generations = [
        generation
        for generation in repository.list_generations(session.id)
        if generation.branch_id == active_branch.id
    ]
    active_generation = repository.get_active_generation(session.id)
    return SessionConversationRead(
        session=to_session_read(session),
        active_branch=to_conversation_branch_read(active_branch),
        branches=[to_conversation_branch_read(branch) for branch in branches],
        messages=[to_message_read(message) for message in messages],
        generations=_build_generation_reads(repository, session.id, generations),
        active_generation_id=active_generation.id if active_generation is not None else None,
        queued_generation_count=repository.queue_size(session.id),
    )


def _build_generation_reads(
    repository: SessionRepository,
    session_id: str,
    generations: list[ChatGeneration],
) -> list[ChatGenerationRead]:
    generation_ids = [generation.id for generation in generations]
    steps_by_generation_id: dict[str, list[GenerationStepRead]] = {}
    for step in repository.list_generation_steps(generation_ids=generation_ids):
        steps_by_generation_id.setdefault(step.generation_id, []).append(
            to_generation_step_read(step)
        )

    queue_positions = {
        generation.id: index
        for index, generation in enumerate(
            repository.list_generations(session_id, statuses={GenerationStatus.QUEUED}),
            start=1,
        )
    }

    reads: list[ChatGenerationRead] = []
    for generation in generations:
        generation_read = to_chat_generation_read(generation)
        generation_read.steps = list(steps_by_generation_id.get(generation.id, []))
        generation_read.queue_position = queue_positions.get(generation.id)
        reads.append(generation_read)
    return reads


def _build_generation_read(
    repository: SessionRepository,
    generation: ChatGeneration,
) -> ChatGenerationRead:
    return _build_generation_reads(repository, generation.session_id, [generation])[0]


def _build_queue_metadata(
    repository: SessionRepository,
    session_id: str,
    generation_id: str | None = None,
) -> tuple[str | None, int | None, int]:
    active_generation = repository.get_active_generation(session_id)
    queued_generation_count = repository.queue_size(session_id)
    queue_position = (
        repository.get_generation_queue_position(session_id, generation_id)
        if generation_id is not None
        else None
    )
    return (
        active_generation.id if active_generation is not None else None,
        queue_position,
        queued_generation_count,
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
    try:
        await start_session_worker_if_needed(
            db_session=db_session,
            session_id=session_id,
            event_broker=event_broker,
            generation_manager=generation_manager,
            chat_runtime=chat_runtime,
            runtime_service=runtime_service,
            skill_service=skill_service,
            mcp_service=mcp_service,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


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
    except GenerationPausedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": str(exc),
                "continuation_token": exc.continuation_token,
                "action": exc.action,
            },
        ) from exc
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


def _catalog_item_trigger(name: str) -> str:
    return name.replace(" ", "-").replace("_", "-")


_MCP_TRIGGER_SANITIZE_PATTERN = re.compile(r"[^a-z0-9_-]+")


def _catalog_mcp_tool_trigger(name: str) -> str:
    normalized = _MCP_TRIGGER_SANITIZE_PATTERN.sub("_", name.strip().casefold()).strip("_")
    return normalized or _catalog_item_trigger(name)


def _ensure_unique_trigger(
    base_trigger: str,
    *,
    used_triggers: set[str],
    fallback_suffix: str,
) -> str:
    if base_trigger not in used_triggers:
        used_triggers.add(base_trigger)
        return base_trigger

    if fallback_suffix:
        fallback_trigger = f"{base_trigger}-{fallback_suffix}"
        if fallback_trigger not in used_triggers:
            used_triggers.add(fallback_trigger)
            return fallback_trigger

    index = 2
    while True:
        candidate = f"{base_trigger}-{index}"
        if candidate not in used_triggers:
            used_triggers.add(candidate)
            return candidate
        index += 1


def _catalog_skill_identifier(skill: SkillAgentSummaryRead) -> str:
    relative_path = skill.resolved_identity.get("relative_path")
    if isinstance(relative_path, str) and relative_path.strip():
        return stable_governance_skill_id(relative_path.strip())
    for candidate in (skill.directory_name, skill.name, skill.id):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _tool_has_required_arguments(input_schema: Mapping[str, object] | None) -> bool:
    if not isinstance(input_schema, Mapping):
        return False
    raw_required = input_schema.get("required")
    if not isinstance(raw_required, list):
        return False
    return any(isinstance(item, str) and item.strip() for item in raw_required)


def _build_builtin_slash_catalog_items() -> list[SlashCatalogItem]:
    build_default_tool_registry = importlib.import_module(
        "app.harness.tools.defaults"
    ).build_default_tool_registry
    registry = build_default_tool_registry(mcp_tools=None, include_swarm_tools=False)
    items: list[SlashCatalogItem] = []
    for tool in registry.list_tools():
        trigger = _catalog_item_trigger(tool.name)
        disabled = _tool_has_required_arguments(tool.input_schema())
        action = SlashActionSelection(
            id=f"builtin:{tool.name}",
            trigger=trigger,
            type="builtin",
            source="builtin",
            display_text=f"/{trigger}",
            invocation=SlashActionInvocation(tool_name=tool.name, arguments={}),
        )
        items.append(
            SlashCatalogItem(
                id=action.id,
                trigger=trigger,
                title=tool.name,
                description=tool.description,
                type="builtin",
                source="builtin",
                badge="Builtin",
                disabled=disabled or None,
                action=action,
            )
        )
    return items


def _build_skill_slash_catalog_items(
    skill_service: SkillService,
    *,
    session_id: str,
) -> list[SlashCatalogItem]:
    items: list[SlashCatalogItem] = []
    del session_id
    for skill in skill_service.list_user_invocable_skills_for_catalog():
        if not skill.invocable:
            continue
        if skill.user_invocable is False:
            continue
        if skill.source_kind == "mcp":
            continue
        trigger_name = _catalog_skill_identifier(skill)
        if not trigger_name:
            continue
        trigger = _catalog_item_trigger(trigger_name)
        description = skill.description.strip() if skill.description.strip() else trigger_name
        action = SlashActionSelection(
            id=f"skill:{trigger_name}",
            trigger=trigger,
            type="skill",
            source="skill",
            display_text=f"/{trigger}",
            invocation=SlashActionInvocation(
                tool_name="execute_skill",
                arguments={"skill_name_or_id": trigger_name},
            ),
        )
        items.append(
            SlashCatalogItem(
                id=action.id,
                trigger=trigger,
                title=skill.name or skill.directory_name or skill.id,
                description=description,
                type="skill",
                source="skill",
                badge="Skill",
                disabled=None,
                action=action,
            )
        )
    return items


def _build_mcp_slash_catalog_items(
    capability_facade: CapabilityFacade,
    *,
    reserved_triggers: set[str] | None = None,
) -> list[SlashCatalogItem]:
    servers = {
        server.id: server for server in capability_facade.list_mcp_servers() if server.enabled
    }
    items: list[SlashCatalogItem] = []
    used_triggers = set(reserved_triggers or ())
    for binding in capability_facade.build_mcp_tool_inventory():
        raw_server_id = binding.get("server_id")
        raw_tool_alias = binding.get("tool_alias")
        raw_tool_name = binding.get("tool_name")
        if not all(
            isinstance(value, str) and value
            for value in (raw_server_id, raw_tool_alias, raw_tool_name)
        ):
            continue
        server_id = str(raw_server_id)
        tool_alias = str(raw_tool_alias)
        tool_name = str(raw_tool_name)
        server = servers.get(server_id)
        if server is None:
            continue
        input_schema = binding.get("input_schema")
        disabled = server.status.value != "connected" or _tool_has_required_arguments(
            input_schema if isinstance(input_schema, Mapping) else None
        )
        trigger = _ensure_unique_trigger(
            _catalog_mcp_tool_trigger(tool_name),
            used_triggers=used_triggers,
            fallback_suffix=_catalog_item_trigger(server.name.casefold()),
        )
        title = (
            binding.get("tool_title")
            if isinstance(binding.get("tool_title"), str) and str(binding.get("tool_title")).strip()
            else tool_name
        )
        description = (
            binding.get("tool_description")
            if isinstance(binding.get("tool_description"), str)
            and str(binding.get("tool_description")).strip()
            else f"Call MCP tool {server.name} / {tool_name}."
        )
        action = SlashActionSelection(
            id=f"mcp:{server_id}:{tool_name}",
            trigger=trigger,
            type="mcp",
            source="mcp",
            display_text=f"/{trigger}",
            invocation=SlashActionInvocation(
                tool_name=tool_alias,
                arguments={},
                mcp_server_id=server_id,
                mcp_tool_name=tool_name,
            ),
        )
        items.append(
            SlashCatalogItem(
                id=action.id,
                trigger=trigger,
                title=str(title),
                description=str(description),
                type="mcp",
                source="mcp",
                badge=server.name,
                disabled=disabled or None,
                action=action,
            )
        )
    return items


def _build_session_slash_catalog(
    *,
    session_id: str,
    skill_service: SkillService,
    mcp_service: MCPService,
) -> list[SlashCatalogItem]:
    capability_facade = CapabilityFacade(skill_service=skill_service, mcp_service=mcp_service)
    builtin_items = _build_builtin_slash_catalog_items()
    skill_items = _build_skill_slash_catalog_items(skill_service, session_id=session_id)
    reserved_triggers = {item.trigger for item in [*builtin_items, *skill_items]}
    mcp_items = _build_mcp_slash_catalog_items(
        capability_facade,
        reserved_triggers=reserved_triggers,
    )
    ordered_items = [
        *builtin_items,
        *skill_items,
        *mcp_items,
    ]
    deduped: list[SlashCatalogItem] = []
    seen_triggers: set[str] = set()
    for item in ordered_items:
        if item.trigger in seen_triggers:
            continue
        seen_triggers.add(item.trigger)
        deduped.append(item)
    return deduped


def _resolve_slash_action_or_422(
    catalog: list[SlashCatalogItem],
    slash_action: SlashActionSelection,
) -> dict[str, object]:
    matching_item = next((item for item in catalog if item.id == slash_action.id), None)
    if matching_item is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid or stale slash_action: '{slash_action.id}'.",
        )
    if matching_item.disabled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Slash action '{slash_action.id}' is currently disabled.",
        )

    expected_action = matching_item.action
    if (
        slash_action.trigger != expected_action.trigger
        or slash_action.type != expected_action.type
        or slash_action.source != expected_action.source
        or slash_action.invocation.tool_name != expected_action.invocation.tool_name
        or slash_action.invocation.arguments != expected_action.invocation.arguments
        or slash_action.invocation.mcp_server_id != expected_action.invocation.mcp_server_id
        or slash_action.invocation.mcp_tool_name != expected_action.invocation.mcp_tool_name
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid or stale slash_action payload for '{slash_action.id}'.",
        )

    return expected_action.model_dump(mode="json")


@router.get("/{session_id}/slash-catalog", response_model=list[SlashCatalogItem])
async def get_session_slash_catalog(
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
    skill_service: SkillService = Depends(get_skill_service),
    mcp_service: MCPService = Depends(get_mcp_service),
) -> list[SlashCatalogItem]:
    repository = SessionRepository(db_session)
    _get_session_or_404(repository, session_id)
    return _build_session_slash_catalog(
        session_id=session_id,
        skill_service=skill_service,
        mcp_service=mcp_service,
    )


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

    normalized_slash_action = None
    if payload.slash_action is not None:
        normalized_slash_action = _resolve_slash_action_or_422(
            _build_session_slash_catalog(
                session_id=session.id,
                skill_service=skill_service,
                mcp_service=mcp_service,
            ),
            payload.slash_action,
        )

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
        metadata_json={
            **(
                {"slash_action": normalized_slash_action}
                if normalized_slash_action is not None
                else {}
            ),
        },
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
            **(
                {"slash_action": normalized_slash_action}
                if normalized_slash_action is not None
                else {}
            ),
        },
    )
    repository.update_message(assistant_message, generation_id=generation.id)
    repository.create_generation_step(
        generation_id=generation.id,
        session_id=session.id,
        message_id=assistant_message.id,
        kind="status",
        phase="planning",
        status="pending",
        state="queued",
        label="Generation queued",
        safe_summary="Generation accepted and queued.",
        metadata_json={"operation": "chat"},
    )

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

    active_generation_id, queue_position, queued_generation_count = _build_queue_metadata(
        repository,
        session.id,
        generation.id,
    )
    future = (
        await generation_manager.register_future(session.id, generation.id)
        if payload.wait_for_completion
        else None
    )
    should_start_worker = await generation_manager.should_start_worker(session.id)
    if active_generation_id is not None or not should_start_worker:
        await _publish_session_updated(
            event_broker,
            session,
            queued_prompt_count=max(1, queued_generation_count),
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

    if not payload.wait_for_completion:
        db_session.expire_all()
        refreshed_session = _get_session_or_404(repository, session_id)
        return ChatResponse(
            session=to_session_read(refreshed_session),
            user_message=to_message_read(user_message),
            assistant_message=to_message_read(assistant_message),
            generation=_build_generation_read(repository, generation),
            branch=to_conversation_branch_read(branch),
            queue_position=queue_position,
            active_generation_id=active_generation_id,
            queued_generation_count=queued_generation_count,
        )

    if future is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Generation future was not registered.",
        )
    await _await_generation_result(
        session_id=session.id, generation_id=generation.id, future=future
    )
    db_session.expire_all()
    refreshed_session = _get_session_or_404(repository, session_id)
    refreshed_generation = repository.get_generation(generation.id)
    refreshed_assistant_message = repository.get_message(assistant_message.id)
    if refreshed_generation is None or refreshed_assistant_message is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Assistant message was not persisted.",
        )
    active_generation_id, queue_position, queued_generation_count = _build_queue_metadata(
        repository,
        session.id,
        generation.id,
    )
    return ChatResponse(
        session=to_session_read(refreshed_session),
        user_message=to_message_read(user_message),
        assistant_message=to_message_read(refreshed_assistant_message),
        generation=_build_generation_read(repository, refreshed_generation),
        branch=to_conversation_branch_read(branch),
        queue_position=queue_position,
        active_generation_id=active_generation_id,
        queued_generation_count=queued_generation_count,
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
    target_metadata = (
        target_message.metadata_json if isinstance(target_message.metadata_json, dict) else {}
    )
    if target_metadata.get("compaction_record") is True or isinstance(
        target_metadata.get("compaction_state"), dict
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Compacted history messages cannot be edited.",
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
    repository.create_generation_step(
        generation_id=generation.id,
        session_id=session.id,
        message_id=assistant_message.id,
        kind="status",
        phase="planning",
        status="pending",
        state="queued",
        label="Generation queued",
        safe_summary="Generation accepted and queued.",
        metadata_json={"operation": "edit"},
    )
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
        generation=_build_generation_read(repository, refreshed_generation),
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
    repository.create_generation_step(
        generation_id=generation.id,
        session_id=session.id,
        message_id=assistant_message.id,
        kind="status",
        phase="planning",
        status="pending",
        state="queued",
        label="Generation queued",
        safe_summary="Generation accepted and queued.",
        metadata_json={"operation": "regenerate"},
    )
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
        generation=_build_generation_read(repository, refreshed_generation),
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
        _clear_generation_continuation_state(
            repository,
            generation,
            assistant_message,
            abort_reason="Queued generation was cancelled.",
        )
        repository.cancel_generation(generation, error_message="Queued generation was cancelled.")
        if assistant_message is not None:
            repository.update_message(
                assistant_message,
                status=MessageStatus.CANCELLED,
                error_message="Queued generation was cancelled.",
            )
            _record_generation_step(
                repository,
                assistant_message=assistant_message,
                kind="status",
                phase="cancelled",
                status="cancelled",
                state="cancelled",
                label="Generation cancelled",
                safe_summary="Queued generation was cancelled.",
                ended_at=utc_now(),
                metadata_json={"generation_id": generation.id},
            )
        repository.close_open_generation_steps(generation.id, status="cancelled", state="cancelled")
        await generation_manager.reject_future(
            session_id,
            generation.id,
            GenerationCancelledError("Queued generation was cancelled."),
        )
    elif generation.status == GenerationStatus.RUNNING:
        _clear_generation_continuation_state(
            repository,
            generation,
            assistant_message,
            abort_reason="Active generation was cancelled.",
        )
        repository.cancel_generation(generation, error_message="Active generation was cancelled.")
        if assistant_message is not None:
            repository.update_message(
                assistant_message,
                status=MessageStatus.CANCELLED,
                error_message="Active generation was cancelled.",
            )
            _record_generation_step(
                repository,
                assistant_message=assistant_message,
                kind="status",
                phase="cancelled",
                status="cancelled",
                state="cancelled",
                label="Generation cancelled",
                safe_summary="Active generation was cancelled.",
                ended_at=utc_now(),
                metadata_json={"generation_id": generation.id},
            )
        repository.close_open_generation_steps(generation.id, status="cancelled", state="cancelled")
        await generation_manager.cancel_generation(session_id, generation.id)
    refreshed_generation = repository.get_generation(generation.id)
    if refreshed_generation is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Generation state was not persisted.",
        )
    return _build_generation_read(repository, refreshed_generation)
