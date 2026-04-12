from __future__ import annotations

import asyncio
import json
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlmodel import Field, SQLModel
from sqlmodel import Session as DBSession

from app.agent.continuation_store import ContinuationStore
from app.agent.token_budget import estimate_token_count
from app.compat.mcp.service import MCPService, get_mcp_service
from app.compat.skills.service import SkillService, get_skill_service
from app.core.api import AckResponse, PaginationMeta, SortMeta, ok_response
from app.core.auth import is_websocket_authorized
from app.core.events import SessionEvent, SessionEventBroker, SessionEventType, get_event_broker
from app.core.settings import Settings, get_settings
from app.db.models import (
    ChatGeneration,
    ChatGenerationRead,
    GenerationStatus,
    GenerationStepRead,
    Message,
    MessageKind,
    MessageRole,
    MessageStatus,
    Session,
    SessionCompactRequest,
    SessionCompactResponse,
    SessionContextWindowBreakdownRead,
    SessionContextWindowRead,
    SessionConversationRead,
    SessionCreate,
    SessionDetail,
    SessionQueueRead,
    SessionRead,
    SessionReplayRead,
    SessionStatus,
    SessionUpdate,
    TerminalJobRead,
    TerminalSessionCreateRequest,
    TerminalSessionRead,
    to_chat_generation_read,
    to_conversation_branch_read,
    to_generation_step_read,
    to_message_read,
    to_run_log_read,
    to_runtime_artifact_read,
    to_session_detail,
    to_session_read,
)
from app.db.repositories import (
    ProjectRepository,
    RunLogRepository,
    RuntimeRepository,
    SessionRepository,
    TerminalRepository,
)
from app.db.session import get_db_session, get_websocket_db_session
from app.harness.compact.service import HarnessCompactService
from app.harness.continuations import (
    ContinuationResolutionError,
    clear_generation_continuation_state,
    normalize_continuation_resolution_input,
    resolve_session_continuation,
)
from app.harness.memory.service import HarnessMemoryService
from app.harness.prompts import HarnessPromptAssembler
from app.harness.session_runner import start_worker_if_needed as start_session_worker_if_needed
from app.harness.state import HarnessRetrievalManifest, HarnessSessionState
from app.services.capabilities import CapabilityFacade
from app.services.chat_runtime import ChatRuntime, get_chat_runtime
from app.services.runtime import RuntimeService, get_runtime_service
from app.services.session_generation import (
    GenerationCancelledError,
    SessionGenerationManager,
    get_generation_manager,
)
from app.services.terminal_sessions import SessionShellService, terminal_audit_payload

terminal_runtime = import_module("app.services.terminal_runtime")
TerminalAlreadyAttachedError = terminal_runtime.TerminalAlreadyAttachedError
TerminalBackendUnavailableError = terminal_runtime.TerminalBackendUnavailableError
TerminalClosedError = terminal_runtime.TerminalClosedError
TerminalNotFoundError = terminal_runtime.TerminalNotFoundError
build_terminal_runtime_service = terminal_runtime.build_terminal_runtime_service

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

DEFAULT_CONTEXT_WINDOW_TOKENS = 400_000
DEFAULT_AUTO_COMPACT_THRESHOLD_RATIO = 0.8


class ContinuationResolveRequest(SQLModel):
    approve: bool | None = None
    approved: bool | None = None
    scope_confirmed: bool | None = None
    user_input: str | None = None
    resolution_payload: dict[str, object] | None = None


class ActiveGenerationInjectRequest(SQLModel):
    content: str = Field(min_length=1, max_length=20000)


class ActiveGenerationInjectResponse(SQLModel):
    session_id: str
    generation_id: str
    delivery: Literal["running_checkpoint", "paused_continuation"]
    queued_injection_count: int = 0


def _get_existing_session(
    repository: SessionRepository,
    session_id: str,
    *,
    include_deleted: bool = False,
) -> Session:
    session = repository.get_session(session_id, include_deleted=include_deleted)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


def _ensure_project_exists(repository: ProjectRepository, project_id: str) -> None:
    project = repository.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")


def _validate_runtime_profile_name(settings: Settings, profile_name: str | None) -> str | None:
    if profile_name is None:
        return None
    if profile_name not in settings.runtime_profiles_json:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown runtime profile '{profile_name}'.",
        )
    return profile_name


def _build_conversation_read(
    repository: SessionRepository, session: Session
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


def _resolve_session_model_name(settings: Settings) -> str:
    if settings.llm_provider == "anthropic":
        configured = settings.anthropic_model
    else:
        configured = settings.llm_default_model
    normalized = (configured or "").strip()
    return normalized or "unknown"


def _resolve_context_window_tokens(model_name: str) -> int:
    normalized = model_name.lower()
    if normalized.startswith("gpt-5.4"):
        return 400_000
    if normalized.startswith("claude"):
        return 200_000
    return DEFAULT_CONTEXT_WINDOW_TOKENS


def _latest_compaction_payload(
    repository: SessionRepository, session_id: str
) -> dict[str, object] | None:
    for event in reversed(repository.list_session_events(session_id, limit=2_000)):
        if event.event_type == SessionEventType.SESSION_CONTEXT_WINDOW_UPDATED.value:
            payload = dict(event.payload_json)
            if payload.get("session_id") == session_id:
                return payload
    return None


def _parse_optional_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _coerce_int(value: object, fallback: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return fallback
    return fallback


def _coerce_float(value: object, fallback: float) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return fallback
    return fallback


def _normalize_breakdown_items(value: object) -> list[SessionContextWindowBreakdownRead]:
    if not isinstance(value, list):
        return []
    items: list[SessionContextWindowBreakdownRead] = []
    for item in value:
        if isinstance(item, SessionContextWindowBreakdownRead):
            items.append(item)
        elif isinstance(item, dict):
            items.append(SessionContextWindowBreakdownRead.model_validate(item))
    return items


def _resolve_context_window_prompt_anchors(
    repository: SessionRepository,
    session: Session,
) -> tuple[Message | None, Message | None]:
    messages = [
        message
        for message in repository.list_messages(
            session.id,
            branch_id=session.active_branch_id,
            include_superseded=False,
        )
        if message.message_kind == MessageKind.MESSAGE
        and message.role in {MessageRole.USER, MessageRole.ASSISTANT}
    ]
    if not messages:
        return None, None
    latest_message = messages[-1]
    latest_assistant = next(
        (message for message in reversed(messages) if message.role == MessageRole.ASSISTANT),
        latest_message,
    )
    latest_user = latest_message if latest_message.role == MessageRole.USER else None
    return latest_user, latest_assistant


def _build_live_context_window_metrics(
    repository: SessionRepository,
    session: Session,
    *,
    settings: Settings,
    skill_service: SkillService,
    mcp_service: MCPService,
) -> tuple[int, list[SessionContextWindowBreakdownRead]]:
    model_name = _resolve_session_model_name(settings)
    context_window_tokens = _resolve_context_window_tokens(model_name)
    input_budget = max(context_window_tokens - settings.llm_max_output_tokens, 1)
    latest_user_message, latest_assistant_message = _resolve_context_window_prompt_anchors(
        repository, session
    )
    if latest_assistant_message is None:
        return 0, []

    prompt_assembler = HarnessPromptAssembler(
        capability_facade=CapabilityFacade(skill_service=skill_service, mcp_service=mcp_service),
        skill_service=skill_service,
        memory_service=HarnessMemoryService(
            base_dir=(Path(settings.runtime_workspace_dir).resolve() / "memory")
        ),
    )
    prompt_assembly = prompt_assembler.build(
        session=session,
        repository=repository,
        user_message=latest_user_message,
        assistant_message=latest_assistant_message,
        branch_id=session.active_branch_id,
        total_token_budget=input_budget,
    )
    prompt_budget = prompt_assembly.prompt_budget.component_tokens
    breakdown_candidates = [
        (
            "system",
            "System",
            prompt_budget.get("core_immutable", 0)
            + prompt_budget.get("safety_scope", 0)
            + prompt_budget.get("role_prompt", 0),
        ),
        (
            "tool_definitions",
            "Capability",
            prompt_budget.get("capability_schema", 0) + prompt_budget.get("capability_prompt", 0),
        ),
        (
            "messages",
            "Messages",
            prompt_budget.get("task_local", 0) + prompt_budget.get("history", 0),
        ),
        (
            "retrieval",
            "Retrieval",
            estimate_token_count(prompt_assembly.memory_context.retrieval_fragment),
        ),
        (
            "memory",
            "Memory",
            estimate_token_count(prompt_assembly.memory_context.memory_fragment),
        ),
    ]
    used_tokens = sum(max(tokens, 0) for _, _, tokens in breakdown_candidates)
    breakdown = [
        SessionContextWindowBreakdownRead(
            key=key,
            label=label,
            estimated_tokens=max(tokens, 0),
            share_ratio=(max(tokens, 0) / context_window_tokens) if context_window_tokens else 0.0,
        )
        for key, label, tokens in breakdown_candidates
        if max(tokens, 0) > 0
    ]
    return used_tokens, breakdown


def _build_context_window_snapshot(
    repository: SessionRepository,
    session: Session,
    *,
    settings: Settings,
    skill_service: SkillService,
    mcp_service: MCPService,
    last_compacted_at_override: datetime | None = None,
    last_compact_boundary_override: str | None = None,
) -> SessionContextWindowRead:
    model_name = _resolve_session_model_name(settings)
    context_window_tokens = _resolve_context_window_tokens(model_name)
    reserved_response_tokens = settings.llm_max_output_tokens
    used_tokens, breakdown = _build_live_context_window_metrics(
        repository,
        session,
        settings=settings,
        skill_service=skill_service,
        mcp_service=mcp_service,
    )
    active_generation = repository.get_active_generation(session.id)
    snapshot = SessionContextWindowRead(
        session_id=session.id,
        model=model_name,
        context_window_tokens=context_window_tokens,
        used_tokens=used_tokens,
        reserved_response_tokens=reserved_response_tokens,
        usage_ratio=(used_tokens / context_window_tokens) if context_window_tokens else 0.0,
        auto_compact_threshold_ratio=DEFAULT_AUTO_COMPACT_THRESHOLD_RATIO,
        last_compacted_at=last_compacted_at_override,
        last_compact_boundary=last_compact_boundary_override,
        can_manual_compact=active_generation is None,
        blocking_reason=("active generation is running" if active_generation is not None else None),
        breakdown=breakdown,
    )
    latest_payload = _latest_compaction_payload(repository, session.id)
    if latest_payload is None:
        return snapshot
    return SessionContextWindowRead(
        session_id=session.id,
        model=snapshot.model,
        context_window_tokens=snapshot.context_window_tokens,
        used_tokens=snapshot.used_tokens,
        reserved_response_tokens=snapshot.reserved_response_tokens,
        usage_ratio=snapshot.usage_ratio,
        auto_compact_threshold_ratio=snapshot.auto_compact_threshold_ratio,
        last_compacted_at=(
            last_compacted_at_override
            if last_compacted_at_override is not None
            else _parse_optional_datetime(latest_payload.get("last_compacted_at"))
        ),
        last_compact_boundary=(
            last_compact_boundary_override
            if last_compact_boundary_override is not None
            else (
                str(latest_payload.get("last_compact_boundary"))
                if latest_payload.get("last_compact_boundary") is not None
                else snapshot.last_compact_boundary
            )
        ),
        can_manual_compact=snapshot.can_manual_compact,
        blocking_reason=snapshot.blocking_reason,
        breakdown=list(snapshot.breakdown),
    )


def _serialize_context_window_snapshot(snapshot: SessionContextWindowRead) -> dict[str, object]:
    return snapshot.model_dump(mode="json")


def _find_latest_assistant_message(
    repository: SessionRepository, session: Session
) -> Message | None:
    for message in reversed(
        repository.list_messages(
            session.id, branch_id=session.active_branch_id, include_superseded=False
        )
    ):
        if message.role == MessageRole.ASSISTANT and message.message_kind == MessageKind.MESSAGE:
            return message
    return None


def _restore_semantic_state_from_messages(messages: list[Message]) -> dict[str, object] | None:
    for message in reversed(messages):
        metadata = getattr(message, "metadata_json", {})
        if not isinstance(metadata, dict):
            continue
        semantic_state = metadata.get("semantic_state")
        if isinstance(semantic_state, dict):
            return {
                "active_hypotheses": [
                    str(item)
                    for item in semantic_state.get("active_hypotheses", [])
                    if isinstance(item, str)
                ],
                "evidence_ids": [
                    str(item)
                    for item in semantic_state.get("evidence_ids", [])
                    if isinstance(item, str)
                ],
                "graph_hints": [
                    dict(item)
                    for item in semantic_state.get("graph_hints", [])
                    if isinstance(item, dict)
                ],
                "artifacts": [
                    str(item)
                    for item in semantic_state.get("artifacts", [])
                    if isinstance(item, str)
                ],
                "recent_entities": [
                    str(item)
                    for item in semantic_state.get("recent_entities", [])
                    if isinstance(item, str)
                ],
                "recent_tools": [
                    str(item)
                    for item in semantic_state.get("recent_tools", [])
                    if isinstance(item, str)
                ],
                "reason": (
                    str(semantic_state.get("reason"))
                    if semantic_state.get("reason") is not None
                    else None
                ),
            }
    return None


def _estimate_message_payload_tokens(messages: list[Message]) -> int:
    return sum(
        estimate_token_count(
            json.dumps(
                {
                    "role": message.role.value,
                    "content": message.content,
                    "attachments": message.attachments_json,
                },
                ensure_ascii=False,
            )
        )
        for message in messages
        if message.message_kind == MessageKind.MESSAGE
        and message.role in {MessageRole.USER, MessageRole.ASSISTANT}
    )


def _persist_manual_compaction_message(
    repository: SessionRepository,
    session: Session,
    *,
    visible_messages: list[Message],
    compact_boundary: str,
    compacted_at: datetime,
    session_state: HarnessSessionState,
) -> Message:
    archived_message_count = session_state.compaction.archived_message_count
    if archived_message_count <= 0:
        raise ValueError("Manual compaction did not archive any persisted messages.")

    archived_messages = visible_messages[:archived_message_count]
    if not archived_messages:
        raise ValueError("Manual compaction missing archived message set.")

    restored_semantic_state = _restore_semantic_state_from_messages(visible_messages)
    compact_message_metadata: dict[str, object] = {
        "summary": "已压缩对话",
        "compact_boundary": compact_boundary,
        "compaction_record": True,
        "compaction_state": {
            "recent_turns": session_state.compaction.recent_turns,
            "last_compacted_turn": session_state.compaction.last_compacted_turn,
            "active_compact_fragment": session_state.compaction.active_compact_fragment,
            "durable_artifact_ref": session_state.compaction.durable_artifact_ref,
            "mode": session_state.compaction.mode,
            "archived_message_count": session_state.compaction.archived_message_count,
        },
    }
    if restored_semantic_state is not None:
        compact_message_metadata["semantic_state"] = restored_semantic_state

    compact_message = repository.create_message(
        session=session,
        role=MessageRole.USER,
        content=session_state.compaction.active_compact_fragment,
        attachments=[],
        branch_id=session.active_branch_id,
        sequence=archived_messages[0].sequence,
        turn_index=archived_messages[0].turn_index,
        metadata_json=compact_message_metadata,
        commit=False,
    )
    compact_message.created_at = compacted_at
    compact_message.completed_at = compacted_at
    repository.db_session.add(compact_message)

    for message in archived_messages:
        message.status = MessageStatus.SUPERSEDED
        repository.db_session.add(message)

    repository.db_session.commit()
    repository.db_session.refresh(compact_message)
    return compact_message


def _manual_compaction_result(
    repository: SessionRepository,
    session: Session,
    *,
    settings: Settings,
) -> SessionCompactResponse:
    messages = repository.list_messages(
        session.id,
        branch_id=session.active_branch_id,
        include_superseded=False,
    )
    visible_messages = [
        message
        for message in messages
        if message.message_kind == MessageKind.MESSAGE
        and message.role in {MessageRole.USER, MessageRole.ASSISTANT}
    ]
    message_payloads = [
        {
            "role": message.role.value,
            "content": message.content,
            **(
                {"attachments": [dict(attachment) for attachment in message.attachments_json]}
                if message.attachments_json
                else {}
            ),
        }
        for message in messages
        if message.message_kind == MessageKind.MESSAGE
        and message.role in {MessageRole.USER, MessageRole.ASSISTANT}
    ]
    before_tokens = sum(
        estimate_token_count(json.dumps(item, ensure_ascii=False)) for item in message_payloads
    )
    memory_service = HarnessMemoryService(
        base_dir=(Path(settings.runtime_workspace_dir).resolve() / "memory")
    )
    memory_key = memory_service.memory_key_for_session(session.id, session.project_id)
    session_state = HarnessSessionState(
        session_id=session.id,
        memory_key=memory_key,
        current_phase=session.current_phase,
        goal=session.goal,
        scenario_type=session.scenario_type,
        retrieval_manifest=HarnessRetrievalManifest(memory_key=memory_key),
    )
    compact_service = HarnessCompactService(memory_service=memory_service)
    compacted_messages = compact_service.maybe_compact(
        messages=message_payloads,
        session_state=session_state,
        render_compact_message=lambda compact_fragment: {
            "role": "assistant",
            "content": "已压缩对话",
            "metadata": {"compact_fragment": compact_fragment},
        },
        turn_count=max(len(message_payloads), 1),
    )
    compacted = session_state.compaction.mode == "full"
    compact_boundary = (
        f"compact-boundary:{session_state.compaction.last_compacted_turn}" if compacted else None
    )
    compacted_at = datetime.now(session.updated_at.tzinfo)
    if compacted and compact_boundary is not None:
        _persist_manual_compaction_message(
            repository,
            session,
            visible_messages=visible_messages,
            compact_boundary=compact_boundary,
            compacted_at=compacted_at,
            session_state=session_state,
        )
        persisted_messages = repository.list_messages(
            session.id,
            branch_id=session.active_branch_id,
            include_superseded=False,
        )
        after_tokens = _estimate_message_payload_tokens(persisted_messages)
    else:
        after_tokens = sum(
            estimate_token_count(json.dumps(item, ensure_ascii=False))
            for item in compacted_messages
        )
    return SessionCompactResponse(
        session_id=session.id,
        mode="manual",
        compacted=compacted,
        compact_boundary=compact_boundary,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        reclaimed_tokens=max(before_tokens - after_tokens, 0),
        summary="已压缩对话" if compacted else "当前上下文暂不需要压缩",
        created_at=compacted_at,
    )


@router.get(
    "",
    response_model=list[SessionRead],
    summary="List sessions",
    description=(
        "Return sessions with optional project/status filters, pagination, "
        "sorting, and fuzzy search."
    ),
)
async def list_sessions(
    include_deleted: bool = Query(default=False),
    project_id: str | None = Query(default=None),
    status_filter: SessionStatus | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    sort_by: Literal["updated_at", "created_at", "title", "status"] = Query(default="updated_at"),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    db_session: DBSession = Depends(get_db_session),
) -> object:
    repository = SessionRepository(db_session)
    offset = (page - 1) * page_size
    sessions = repository.list_sessions(
        include_deleted=include_deleted,
        project_id=project_id,
        status=status_filter,
        query=q,
        offset=offset,
        limit=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    total = repository.count_sessions(
        include_deleted=include_deleted,
        project_id=project_id,
        status=status_filter,
        query=q,
    )
    return ok_response(
        [to_session_read(session).model_dump(mode="json") for session in sessions],
        pagination=PaginationMeta(page=page, page_size=page_size, total=total),
        sort=SortMeta(by=sort_by, direction=sort_order),
    )


@router.post(
    "",
    response_model=SessionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create session",
    description=(
        "Create a session that can optionally be linked to a project and runtime policy metadata."
    ),
)
async def create_session(
    payload: SessionCreate | None = None,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    settings: Settings = Depends(get_settings),
) -> object:
    repository = SessionRepository(db_session)
    project_repository = ProjectRepository(db_session)
    if payload is not None and payload.project_id is not None:
        _ensure_project_exists(project_repository, payload.project_id)
    session = repository.create_session(
        title=payload.title if payload is not None else None,
        project_id=payload.project_id if payload is not None else None,
        goal=payload.goal if payload is not None else None,
        scenario_type=payload.scenario_type if payload is not None else None,
        current_phase=payload.current_phase if payload is not None else None,
        runtime_policy_json=payload.runtime_policy_json if payload is not None else None,
        runtime_profile_name=(
            _validate_runtime_profile_name(settings, payload.runtime_profile_name)
            if payload is not None
            else settings.runtime_default_profile_name
        )
        or settings.runtime_default_profile_name,
    )
    session_read = to_session_read(session)
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_CREATED,
            session_id=session.id,
            payload={"title": session.title, "status": session.status.value},
        )
    )
    return ok_response(session_read.model_dump(mode="json"), status_code=201)


@router.get(
    "/{session_id}",
    response_model=SessionDetail,
    summary="Get session",
    description="Return a session record and its persisted message history.",
)
async def get_session(
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
) -> object:
    repository = SessionRepository(db_session)
    session = _get_existing_session(repository, session_id)
    messages = repository.list_messages(session_id)
    return ok_response(to_session_detail(session, messages).model_dump(mode="json"))


@router.patch("/{session_id}", response_model=SessionRead)
async def update_session(
    session_id: str,
    payload: SessionUpdate,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    settings: Settings = Depends(get_settings),
) -> object:
    repository = SessionRepository(db_session)
    project_repository = ProjectRepository(db_session)
    session = _get_existing_session(repository, session_id)
    if payload.project_id is not None:
        _ensure_project_exists(project_repository, payload.project_id)
    runtime_profile_name = _validate_runtime_profile_name(settings, payload.runtime_profile_name)
    if (
        runtime_profile_name is not None
        and session.runtime_profile_name is not None
        and runtime_profile_name != session.runtime_profile_name
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="runtime_profile_name is immutable after session creation.",
        )
    updated_session = repository.update_session(
        session,
        title=payload.title,
        status=payload.status,
        project_id=payload.project_id,
        active_branch_id=payload.active_branch_id,
        goal=payload.goal,
        scenario_type=payload.scenario_type,
        current_phase=payload.current_phase,
        runtime_policy_json=payload.runtime_policy_json,
        runtime_profile_name=runtime_profile_name,
    )
    session_read = to_session_read(updated_session)
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_UPDATED,
            session_id=updated_session.id,
            payload=session_read.model_dump(mode="json"),
        )
    )
    return ok_response(session_read.model_dump(mode="json"))


@router.post("/{session_id}/pause", response_model=SessionRead)
async def pause_session(
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
) -> object:
    repository = SessionRepository(db_session)
    session = _get_existing_session(repository, session_id)
    updated_session = repository.update_session(session, status=SessionStatus.PAUSED)
    session_read = to_session_read(updated_session)
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_UPDATED,
            session_id=updated_session.id,
            payload={"title": updated_session.title, "status": updated_session.status.value},
        )
    )
    return ok_response(session_read.model_dump(mode="json"))


@router.post("/{session_id}/resume", response_model=SessionRead)
async def resume_session(
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    chat_runtime: ChatRuntime = Depends(get_chat_runtime),
    runtime_service: RuntimeService = Depends(get_runtime_service),
    skill_service: SkillService = Depends(get_skill_service),
    mcp_service: MCPService = Depends(get_mcp_service),
    generation_manager: SessionGenerationManager = Depends(get_generation_manager),
) -> object:
    repository = SessionRepository(db_session)
    session = _get_existing_session(repository, session_id)
    updated_session = repository.update_session(session, status=SessionStatus.RUNNING)
    session_read = to_session_read(updated_session)
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_UPDATED,
            session_id=updated_session.id,
            payload={"title": updated_session.title, "status": updated_session.status.value},
        )
    )
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
    return ok_response(session_read.model_dump(mode="json"))


@router.post("/{session_id}/continuations/{continuation_token}/resolve")
async def resolve_continuation(
    session_id: str,
    continuation_token: str,
    payload: ContinuationResolveRequest,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    generation_manager: SessionGenerationManager = Depends(get_generation_manager),
) -> object:
    repository = SessionRepository(db_session)
    session = _get_existing_session(repository, session_id)
    request = normalize_continuation_resolution_input(
        approve=payload.approve,
        approved=payload.approved,
        scope_confirmed=payload.scope_confirmed,
        user_input=payload.user_input,
        resolution_payload=payload.resolution_payload,
    )
    try:
        resolved = await resolve_session_continuation(
            repository=repository,
            session=session,
            continuation_token=continuation_token,
            request=request,
            event_broker=event_broker,
            generation_manager=generation_manager,
        )
    except ContinuationResolutionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return ok_response(
        {
            "session": to_session_read(resolved.session).model_dump(mode="json"),
            "continuation_token": continuation_token,
            "status": "resolved",
            "resolution": resolved.resolution.to_state(),
        }
    )


@router.post(
    "/{session_id}/generations/active/inject",
    response_model=ActiveGenerationInjectResponse,
)
async def inject_active_generation_context(
    session_id: str,
    payload: ActiveGenerationInjectRequest,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    generation_manager: SessionGenerationManager = Depends(get_generation_manager),
) -> object:
    repository = SessionRepository(db_session)
    session = _get_existing_session(repository, session_id)
    active_generation = repository.get_active_generation(session_id)
    if active_generation is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "no_active_generation", "message": "当前没有可注入的活跃生成。"},
        )

    normalized_content = payload.content.strip()
    if not normalized_content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "empty_injection", "message": "注入内容不能为空。"},
        )

    if session.status == SessionStatus.PAUSED:
        continuation_store = ContinuationStore()
        pause_state = active_generation.metadata_json.get("pause_state")
        if not isinstance(pause_state, dict):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "no_pending_continuation",
                    "message": "当前暂停态没有可继续的交互式续传。",
                },
            )
        active_contract = continuation_store.active_contract(dict(pause_state))
        if active_contract is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "no_pending_continuation",
                    "message": "当前暂停态没有可继续的交互式续传。",
                },
            )
        if active_contract.protocol_kind != "interaction":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "approval_required",
                    "message": "当前暂停需要显式审批，不能直接注入自由文本上下文。",
                },
            )
        request = normalize_continuation_resolution_input(
            approve=None,
            approved=None,
            scope_confirmed=True,
            user_input=normalized_content,
            resolution_payload=None,
        )
        try:
            await resolve_session_continuation(
                repository=repository,
                session=session,
                continuation_token=active_contract.continuation_token,
                request=request,
                event_broker=event_broker,
                generation_manager=generation_manager,
            )
        except ContinuationResolutionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        return ok_response(
            ActiveGenerationInjectResponse(
                session_id=session_id,
                generation_id=active_generation.id,
                delivery="paused_continuation",
                queued_injection_count=0,
            ).model_dump(mode="json")
        )

    queued_injection_count = await generation_manager.enqueue_injection(
        session_id,
        generation_id=active_generation.id,
        content=normalized_content,
    )
    if queued_injection_count is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "active_generation_unavailable",
                "message": "当前活跃生成尚未准备好接收注入，请稍后重试。",
            },
        )

    return ok_response(
        ActiveGenerationInjectResponse(
            session_id=session_id,
            generation_id=active_generation.id,
            delivery="running_checkpoint",
            queued_injection_count=queued_injection_count,
        ).model_dump(mode="json")
    )


@router.post("/{session_id}/cancel", response_model=SessionRead)
async def cancel_session(
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    generation_manager: SessionGenerationManager = Depends(get_generation_manager),
) -> object:
    repository = SessionRepository(db_session)
    session = _get_existing_session(repository, session_id)
    active_generation = repository.get_active_generation(session_id)
    if active_generation is not None:
        active_assistant_message = repository.get_message(active_generation.assistant_message_id)
        clear_generation_continuation_state(
            repository,
            active_generation,
            active_assistant_message,
            abort_reason="Active generation was cancelled.",
        )
        repository.cancel_generation(
            active_generation, error_message="Active generation was cancelled."
        )
        assistant_message = active_assistant_message
        if assistant_message is not None:
            repository.update_message(
                assistant_message,
                status=MessageStatus.CANCELLED,
                error_message="Active generation was cancelled.",
            )
        repository.close_open_generation_steps(
            active_generation.id, status="cancelled", state="cancelled"
        )
        await generation_manager.cancel_generation(session_id, active_generation.id)
        await generation_manager.reject_future(
            session_id,
            active_generation.id,
            GenerationCancelledError("Active generation was cancelled."),
        )

    queued_generations = repository.cancel_queued_generations(
        session_id,
        error_message="Queued generation was cancelled.",
    )
    for queued_generation in queued_generations:
        assistant_message = repository.get_message(queued_generation.assistant_message_id)
        clear_generation_continuation_state(
            repository,
            queued_generation,
            assistant_message,
            abort_reason="Queued generation was cancelled.",
        )
        if assistant_message is not None:
            repository.update_message(
                assistant_message,
                status=MessageStatus.CANCELLED,
                error_message="Queued generation was cancelled.",
            )
        repository.close_open_generation_steps(
            queued_generation.id, status="cancelled", state="cancelled"
        )
    await generation_manager.reject_pending(
        session_id,
        GenerationCancelledError("Session generation queue was cancelled."),
        exclude_generation_ids={active_generation.id} if active_generation is not None else None,
    )
    updated_session = repository.update_session(session, status=SessionStatus.CANCELLED)
    session_read = to_session_read(updated_session)
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_UPDATED,
            session_id=updated_session.id,
            payload={**session_read.model_dump(mode="json"), "queued_prompt_count": 0},
        )
    )
    return ok_response(session_read.model_dump(mode="json"))


@router.delete("/{session_id}", status_code=status.HTTP_200_OK)
async def delete_session(
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
) -> object:
    repository = SessionRepository(db_session)
    session = _get_existing_session(repository, session_id)
    deleted_session = repository.soft_delete_session(session)
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_DELETED,
            session_id=deleted_session.id,
            payload={"status": deleted_session.status.value},
        )
    )
    return ok_response(AckResponse().model_dump(mode="json"))


@router.post("/{session_id}/restore", response_model=SessionRead)
async def restore_session(
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
) -> object:
    repository = SessionRepository(db_session)
    session = _get_existing_session(repository, session_id, include_deleted=True)
    restored_session = repository.restore_session(session)
    session_read = to_session_read(restored_session)
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_RESTORED,
            session_id=restored_session.id,
            payload={"title": restored_session.title, "status": restored_session.status.value},
        )
    )
    return ok_response(session_read.model_dump(mode="json"))


@router.get(
    "/{session_id}/conversation",
    response_model=SessionConversationRead,
    summary="Get session conversation",
    description="Return the active branch conversation, branch metadata, and visible messages.",
)
async def get_session_conversation(
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
) -> object:
    repository = SessionRepository(db_session)
    session = _get_existing_session(repository, session_id)
    return ok_response(_build_conversation_read(repository, session).model_dump(mode="json"))


@router.get(
    "/{session_id}/queue",
    response_model=SessionQueueRead,
    summary="Get generation queue",
    description="Return the active generation and queued durable chat generations for the session.",
)
async def get_session_queue(
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
) -> object:
    repository = SessionRepository(db_session)
    session = _get_existing_session(repository, session_id)
    active_generation = repository.get_active_generation(session.id)
    queued_generations = repository.list_generations(
        session.id,
        statuses={GenerationStatus.QUEUED},
    )
    payload = SessionQueueRead(
        session=to_session_read(session),
        active_generation=(
            _build_generation_reads(repository, session.id, [active_generation])[0]
            if active_generation is not None
            else None
        ),
        queued_generations=_build_generation_reads(repository, session.id, queued_generations),
        active_generation_id=active_generation.id if active_generation is not None else None,
        queued_generation_count=len(queued_generations),
    )
    return ok_response(payload.model_dump(mode="json"))


@router.get(
    "/{session_id}/context-window",
    response_model=SessionContextWindowRead,
    summary="Get session context window usage",
)
async def get_session_context_window(
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    skill_service: SkillService = Depends(get_skill_service),
    mcp_service: MCPService = Depends(get_mcp_service),
) -> object:
    repository = SessionRepository(db_session)
    session = _get_existing_session(repository, session_id)
    snapshot = _build_context_window_snapshot(
        repository,
        session,
        settings=settings,
        skill_service=skill_service,
        mcp_service=mcp_service,
    )
    return ok_response(snapshot.model_dump(mode="json"))


@router.post(
    "/{session_id}/compact",
    response_model=SessionCompactResponse,
    summary="Compact session context",
)
async def compact_session_context(
    session_id: str,
    payload: SessionCompactRequest,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    settings: Settings = Depends(get_settings),
    skill_service: SkillService = Depends(get_skill_service),
    mcp_service: MCPService = Depends(get_mcp_service),
) -> object:
    repository = SessionRepository(db_session)
    session = _get_existing_session(repository, session_id)
    active_generation = repository.get_active_generation(session.id)
    if active_generation is not None:
        failure_payload = {
            "mode": payload.mode,
            "summary": "上下文压缩失败",
            "error": "active generation is running",
        }
        await event_broker.publish(
            SessionEvent(
                type=SessionEventType.SESSION_COMPACTION_FAILED,
                session_id=session.id,
                payload=failure_payload,
            )
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=failure_payload,
        )

    result = _manual_compaction_result(repository, session, settings=settings)
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_COMPACTION_COMPLETED,
            session_id=session.id,
            payload=result.model_dump(mode="json"),
        )
    )

    updated_snapshot = _build_context_window_snapshot(
        repository,
        session,
        settings=settings,
        skill_service=skill_service,
        mcp_service=mcp_service,
        last_compacted_at_override=(result.created_at if result.compacted else None),
        last_compact_boundary_override=(result.compact_boundary if result.compacted else None),
    )
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_CONTEXT_WINDOW_UPDATED,
            session_id=session.id,
            payload=_serialize_context_window_snapshot(updated_snapshot),
        )
    )
    return ok_response(result.model_dump(mode="json"))


@router.get(
    "/{session_id}/replay",
    response_model=SessionReplayRead,
    summary="Get session replay",
    description="Return all branches, messages, and generation records for replay/history tooling.",
)
async def get_session_replay(
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
) -> object:
    repository = SessionRepository(db_session)
    session = _get_existing_session(repository, session_id)
    payload = SessionReplayRead(
        session=to_session_read(session),
        branches=[
            to_conversation_branch_read(branch) for branch in repository.list_branches(session.id)
        ],
        messages=[to_message_read(message) for message in repository.list_all_messages(session.id)],
        generations=_build_generation_reads(
            repository,
            session.id,
            repository.list_generations(session.id),
        ),
    )
    return ok_response(payload.model_dump(mode="json"))


@router.get(
    "/{session_id}/history",
    summary="Get session history",
    description=(
        "Return structured RunLog history entries for the session with filtering and pagination."
    ),
)
async def get_session_history(
    session_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    level: str | None = Query(default=None),
    source: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    db_session: DBSession = Depends(get_db_session),
) -> object:
    session_repository = SessionRepository(db_session)
    _get_existing_session(session_repository, session_id, include_deleted=True)
    repository = RunLogRepository(db_session)
    offset = (page - 1) * page_size
    history = repository.list_logs(
        session_id=session_id,
        level=level,
        source=source,
        event_type=event_type,
        query=q,
        sort_order=sort_order,
        offset=offset,
        limit=page_size,
    )
    total = repository.count_logs(
        session_id=session_id,
        level=level,
        source=source,
        event_type=event_type,
        query=q,
    )
    return ok_response(
        [to_run_log_read(entry).model_dump(mode="json") for entry in history],
        pagination=PaginationMeta(page=page, page_size=page_size, total=total),
        sort=SortMeta(by="created_at", direction=sort_order),
    )


@router.get(
    "/{session_id}/artifacts",
    summary="Get session artifacts",
    description="Return runtime artifacts linked to the session with filtering and pagination.",
)
async def get_session_artifacts(
    session_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    q: str | None = Query(default=None),
    sort_by: Literal["created_at", "relative_path"] = Query(default="created_at"),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    db_session: DBSession = Depends(get_db_session),
) -> object:
    session_repository = SessionRepository(db_session)
    _get_existing_session(session_repository, session_id, include_deleted=True)
    repository = RuntimeRepository(db_session)
    offset = (page - 1) * page_size
    artifacts = repository.list_artifacts(
        session_id=session_id,
        query=q,
        offset=offset,
        limit=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    total = repository.count_artifacts(session_id=session_id, query=q)
    return ok_response(
        [to_runtime_artifact_read(artifact).model_dump(mode="json") for artifact in artifacts],
        pagination=PaginationMeta(page=page, page_size=page_size, total=total),
        sort=SortMeta(by=sort_by, direction=sort_order),
    )


@router.get(
    "/{session_id}/terminals",
    response_model=list[TerminalSessionRead],
    summary="List session terminals",
    description="Return persisted terminal-session metadata for the session.",
)
async def list_session_terminals(
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
) -> object:
    session_repository = SessionRepository(db_session)
    _get_existing_session(session_repository, session_id, include_deleted=True)
    service = SessionShellService(TerminalRepository(db_session), RunLogRepository(db_session))
    terminals = service.list_terminals(session_id=session_id)
    return ok_response([terminal.model_dump(mode="json", by_alias=True) for terminal in terminals])


@router.post(
    "/{session_id}/terminals",
    response_model=TerminalSessionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create session terminal",
    description="Persist terminal-session metadata for later shell workbench phases.",
)
async def create_session_terminal(
    session_id: str,
    payload: TerminalSessionCreateRequest,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
) -> object:
    session_repository = SessionRepository(db_session)
    session = _get_existing_session(session_repository, session_id)
    service = SessionShellService(TerminalRepository(db_session), RunLogRepository(db_session))
    result = service.create_terminal(session=session, payload=payload)
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.TERMINAL_SESSION_CREATED,
            session_id=session.id,
            payload=terminal_audit_payload(result.terminal),
        )
    )
    return ok_response(result.terminal.model_dump(mode="json", by_alias=True), status_code=201)


@router.get(
    "/{session_id}/terminals/{terminal_id}",
    response_model=TerminalSessionRead,
    summary="Get session terminal",
    description="Return terminal-session metadata by id.",
)
async def get_session_terminal(
    session_id: str,
    terminal_id: str,
    db_session: DBSession = Depends(get_db_session),
) -> object:
    session_repository = SessionRepository(db_session)
    _get_existing_session(session_repository, session_id, include_deleted=True)
    service = SessionShellService(TerminalRepository(db_session), RunLogRepository(db_session))
    terminal = service.get_terminal(session_id=session_id, terminal_id=terminal_id)
    if terminal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Terminal not found")
    return ok_response(terminal.model_dump(mode="json", by_alias=True))


@router.post(
    "/{session_id}/terminals/{terminal_id}/close",
    response_model=TerminalSessionRead,
    summary="Close session terminal",
    description="Mark terminal-session metadata as closed without simulating PTY persistence.",
)
async def close_session_terminal(
    request: Request,
    session_id: str,
    terminal_id: str,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
) -> object:
    session_repository = SessionRepository(db_session)
    session = _get_existing_session(session_repository, session_id)
    runtime_service = build_terminal_runtime_service(app=request.app, event_broker=event_broker)
    live_closed = await runtime_service.close_live_terminal(
        session_id=session_id,
        terminal_id=terminal_id,
    )
    if live_closed:
        refreshed_terminal = SessionShellService(
            TerminalRepository(db_session),
            RunLogRepository(db_session),
        ).get_terminal(session_id=session_id, terminal_id=terminal_id)
        if refreshed_terminal is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Terminal not found")
        return ok_response(refreshed_terminal.model_dump(mode="json", by_alias=True))

    service = SessionShellService(TerminalRepository(db_session), RunLogRepository(db_session))
    result = service.close_terminal(session=session, terminal_id=terminal_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Terminal not found")
    if result.changed:
        await event_broker.publish(
            SessionEvent(
                type=SessionEventType.TERMINAL_SESSION_CLOSED,
                session_id=session.id,
                payload=terminal_audit_payload(result.terminal),
            )
        )
    return ok_response(result.terminal.model_dump(mode="json", by_alias=True))


@router.websocket("/{session_id}/terminals/{terminal_id}/stream")
async def stream_session_terminal(
    websocket: WebSocket,
    session_id: str,
    terminal_id: str,
    cols: int = Query(default=80, ge=1),
    rows: int = Query(default=24, ge=1),
    db_session: DBSession = Depends(get_websocket_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    settings: Settings = Depends(get_settings),
) -> None:
    authorized, reason = is_websocket_authorized(
        websocket,
        settings,
        allow_query_params=False,
    )
    if not authorized:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=reason or "Unauthorized",
        )

    try:
        session_repository = SessionRepository(db_session)
        session = session_repository.get_session(session_id, include_deleted=True)
    finally:
        db_session.close()

    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    runtime_service = build_terminal_runtime_service(app=websocket.app, event_broker=event_broker)
    try:
        handle = await runtime_service.connect(
            session_id=session_id,
            terminal_id=terminal_id,
            cols=cols,
            rows=rows,
        )
    except TerminalAlreadyAttachedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (TerminalNotFoundError, TerminalClosedError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TerminalBackendUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    await websocket.accept()

    detached = False

    async def _receive_client_frame() -> tuple[str, object | None]:
        try:
            return "frame", await websocket.receive_json()
        except WebSocketDisconnect:
            return "disconnect", None
        except json.JSONDecodeError:
            return "invalid_json", None

    try:
        while True:
            receive_task = asyncio.create_task(_receive_client_frame())
            send_task = asyncio.create_task(handle.queue.get())
            done, pending = await asyncio.wait(
                {receive_task, send_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            should_close = False
            if send_task in done:
                frame = send_task.result()
                await websocket.send_json(frame)
                if frame.get("type") == "closed":
                    should_close = True
            if receive_task in done:
                receive_kind, receive_payload = receive_task.result()
                if receive_kind == "disconnect":
                    if should_close or handle.closed.is_set():
                        return
                    await runtime_service.mark_detached(handle)
                    detached = True
                    return
                if receive_kind == "invalid_json":
                    if should_close:
                        return
                    await runtime_service.emit_protocol_error(
                        handle,
                        "terminal frames must be valid JSON",
                    )
                    continue
                frame = receive_payload
                if not isinstance(frame, dict):
                    if should_close:
                        return
                    await runtime_service.emit_protocol_error(
                        handle,
                        "terminal frames must be JSON objects",
                    )
                    continue
                await runtime_service.handle_client_frame(handle, frame)
            if should_close:
                return
    except WebSocketDisconnect:
        await runtime_service.mark_detached(handle)
        detached = True
        return
    finally:
        if not detached and not handle.closed.is_set():
            await runtime_service.mark_detached(handle)


@router.get(
    "/{session_id}/terminal-jobs",
    response_model=list[TerminalJobRead],
    summary="List session terminal jobs",
    description="Return persisted terminal-job metadata for the session.",
)
async def list_session_terminal_jobs(
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
) -> object:
    session_repository = SessionRepository(db_session)
    _get_existing_session(session_repository, session_id, include_deleted=True)
    service = SessionShellService(TerminalRepository(db_session), RunLogRepository(db_session))
    jobs = service.list_terminal_jobs(session_id=session_id)
    return ok_response([job.model_dump(mode="json", by_alias=True) for job in jobs])


@router.get(
    "/{session_id}/terminal-jobs/{job_id}",
    response_model=TerminalJobRead,
    summary="Get session terminal job",
    description="Return terminal-job metadata by id.",
)
async def get_session_terminal_job(
    session_id: str,
    job_id: str,
    db_session: DBSession = Depends(get_db_session),
) -> object:
    session_repository = SessionRepository(db_session)
    _get_existing_session(session_repository, session_id, include_deleted=True)
    service = SessionShellService(TerminalRepository(db_session), RunLogRepository(db_session))
    job = service.get_terminal_job(session_id=session_id, job_id=job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Terminal job not found")
    return ok_response(job.model_dump(mode="json", by_alias=True))


@router.websocket("/{session_id}/events")
async def stream_session_events(
    websocket: WebSocket,
    session_id: str,
    cursor: int | None = Query(default=None, ge=0),
    db_session: DBSession = Depends(get_websocket_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    settings: Settings = Depends(get_settings),
) -> None:
    authorized, reason = is_websocket_authorized(websocket, settings)
    if not authorized:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=reason or "Unauthorized",
        )

    try:
        repository = SessionRepository(db_session)
        session = repository.get_session(session_id, include_deleted=True)
    finally:
        db_session.close()

    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    await websocket.accept()

    queue = await event_broker.subscribe(session_id)
    replay_cursor = cursor
    last_cursor_sent = replay_cursor
    try:
        if replay_cursor is not None:
            replay_events = await event_broker.replay(session_id, after_cursor=replay_cursor)
            for replay_event in replay_events:
                await websocket.send_json(replay_event.model_dump(mode="json"))
                if replay_event.cursor is not None:
                    last_cursor_sent = replay_event.cursor
        while True:
            event = await queue.get()
            if (
                last_cursor_sent is not None
                and event.cursor is not None
                and event.cursor <= last_cursor_sent
            ):
                continue
            await websocket.send_json(event.model_dump(mode="json"))
            if event.cursor is not None:
                last_cursor_sent = event.cursor
    except WebSocketDisconnect:
        return
    finally:
        await event_broker.unsubscribe(session_id, queue)
