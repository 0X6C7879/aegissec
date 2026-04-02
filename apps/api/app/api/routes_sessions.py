from __future__ import annotations

from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlmodel import Session as DBSession

from app.core.api import AckResponse, PaginationMeta, SortMeta, ok_response
from app.core.events import SessionEvent, SessionEventBroker, SessionEventType, get_event_broker
from app.core.settings import Settings, get_settings
from app.db.models import (
    GenerationStatus,
    MessageStatus,
    Session,
    SessionConversationRead,
    SessionCreate,
    SessionDetail,
    SessionQueueRead,
    SessionRead,
    SessionReplayRead,
    SessionStatus,
    SessionUpdate,
    to_chat_generation_read,
    to_conversation_branch_read,
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
)
from app.db.session import get_db_session, get_websocket_db_session
from app.services.session_generation import (
    GenerationCancelledError,
    SessionGenerationManager,
    get_generation_manager,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


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
    return ok_response(session_read.model_dump(mode="json"))


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
        repository.cancel_generation(
            active_generation, error_message="Active generation was cancelled."
        )
        assistant_message = repository.get_message(active_generation.assistant_message_id)
        if assistant_message is not None:
            repository.update_message(
                assistant_message,
                status=MessageStatus.CANCELLED,
                error_message="Active generation was cancelled.",
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
        if assistant_message is not None:
            repository.update_message(
                assistant_message,
                status=MessageStatus.CANCELLED,
                error_message="Queued generation was cancelled.",
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
            to_chat_generation_read(active_generation) if active_generation is not None else None
        ),
        queued_generations=[
            to_chat_generation_read(generation) for generation in queued_generations
        ],
    )
    return ok_response(payload.model_dump(mode="json"))


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
        generations=[
            to_chat_generation_read(generation)
            for generation in repository.list_generations(session.id)
        ],
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


@router.websocket("/{session_id}/events")
async def stream_session_events(
    websocket: WebSocket,
    session_id: str,
    db_session: DBSession = Depends(get_websocket_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
) -> None:
    try:
        repository = SessionRepository(db_session)
        session = repository.get_session(session_id, include_deleted=True)
    finally:
        db_session.close()

    await websocket.accept()
    if session is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Session not found")
        return

    queue = await event_broker.subscribe(session_id)
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event.model_dump(mode="json"))
    except WebSocketDisconnect:
        return
    finally:
        await event_broker.unsubscribe(session_id, queue)
