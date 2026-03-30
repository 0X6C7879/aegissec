from __future__ import annotations

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlmodel import Session as DBSession

from app.core.events import SessionEvent, SessionEventBroker, SessionEventType, get_event_broker
from app.db.models import (
    Session,
    SessionCreate,
    SessionDetail,
    SessionRead,
    SessionUpdate,
    to_session_detail,
    to_session_read,
)
from app.db.repositories import SessionRepository
from app.db.session import get_db_session

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


@router.get("", response_model=list[SessionRead])
async def list_sessions(
    include_deleted: bool = Query(default=False),
    db_session: DBSession = Depends(get_db_session),
) -> list[SessionRead]:
    repository = SessionRepository(db_session)
    sessions = repository.list_sessions(include_deleted=include_deleted)
    return [to_session_read(session) for session in sessions]


@router.post("", response_model=SessionRead, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreate | None = None,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
) -> SessionRead:
    repository = SessionRepository(db_session)
    session = repository.create_session(title=payload.title if payload is not None else None)
    session_read = to_session_read(session)
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_CREATED,
            session_id=session.id,
            payload={"title": session.title, "status": session.status.value},
        )
    )
    return session_read


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
) -> SessionDetail:
    repository = SessionRepository(db_session)
    session = _get_existing_session(repository, session_id)
    messages = repository.list_messages(session_id)
    return to_session_detail(session, messages)


@router.patch("/{session_id}", response_model=SessionRead)
async def update_session(
    session_id: str,
    payload: SessionUpdate,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
) -> SessionRead:
    repository = SessionRepository(db_session)
    session = _get_existing_session(repository, session_id)
    updated_session = repository.update_session(session, title=payload.title, status=payload.status)
    session_read = to_session_read(updated_session)
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_UPDATED,
            session_id=updated_session.id,
            payload={"title": updated_session.title, "status": updated_session.status.value},
        )
    )
    return session_read


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
) -> Response:
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
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{session_id}/restore", response_model=SessionRead)
async def restore_session(
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
) -> SessionRead:
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
    return session_read


@router.websocket("/{session_id}/events")
async def stream_session_events(
    websocket: WebSocket,
    session_id: str,
    db_session: DBSession = Depends(get_db_session),
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
