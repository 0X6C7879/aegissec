from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy.exc import TimeoutError as SQLAlchemyPoolTimeoutError
from sqlmodel import Field, SQLModel
from sqlmodel import Session as DBSession

from app.db.models import utc_now

logger = logging.getLogger("aegissec.api")


class SessionEventType(str, Enum):
    SESSION_CREATED = "session.created"
    SESSION_UPDATED = "session.updated"
    SESSION_DELETED = "session.deleted"
    SESSION_RESTORED = "session.restored"
    TERMINAL_SESSION_CREATED = "terminal.session.created"
    TERMINAL_SESSION_CLOSED = "terminal.session.closed"
    SESSION_CONTEXT_WINDOW_UPDATED = "session.context_window.updated"
    SESSION_COMPACTION_COMPLETED = "session.compaction.completed"
    SESSION_COMPACTION_FAILED = "session.compaction.failed"
    GENERATION_STARTED = "generation.started"
    GENERATION_FAILED = "generation.failed"
    GENERATION_CANCELLED = "generation.cancelled"
    MESSAGE_CREATED = "message.created"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_UPDATED = "message.updated"
    MESSAGE_COMPLETED = "message.completed"
    ASSISTANT_SUMMARY = "assistant.summary"
    ASSISTANT_TRACE = "assistant.trace"
    TOOL_CALL_STARTED = "tool.call.started"
    TOOL_CALL_FINISHED = "tool.call.finished"
    TOOL_CALL_FAILED = "tool.call.failed"
    WORKFLOW_RUN_STARTED = "workflow.run.started"
    WORKFLOW_STAGE_CHANGED = "workflow.stage.changed"
    WORKFLOW_TASK_UPDATED = "workflow.task.updated"
    TASK_PLANNED = "task.planned"
    TASK_STARTED = "task.started"
    TASK_FINISHED = "task.finished"
    APPROVAL_REQUIRED = "workflow.approval.required"
    WORKFLOW_APPROVAL_REQUIRED = "workflow.approval.required"
    GRAPH_UPDATED = "graph.updated"


class SessionEvent(SQLModel):
    type: SessionEventType
    session_id: str
    cursor: int | None = None
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any]


class SessionEventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[SessionEvent]]] = {}
        self._lock = asyncio.Lock()
        self._session_factory: Callable[[], DBSession] | None = None

    def configure_persistence(self, session_factory: Callable[[], DBSession]) -> None:
        self._session_factory = session_factory

    async def subscribe(self, session_id: str) -> asyncio.Queue[SessionEvent]:
        queue: asyncio.Queue[SessionEvent] = asyncio.Queue()
        async with self._lock:
            subscribers = self._subscribers.setdefault(session_id, set())
            subscribers.add(queue)
        return queue

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue[SessionEvent]) -> None:
        async with self._lock:
            subscribers = self._subscribers.get(session_id)
            if subscribers is None:
                return

            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(session_id, None)

    async def publish(self, event: SessionEvent) -> None:
        persisted_event = self._persist_event(event)
        async with self._lock:
            subscribers = list(self._subscribers.get(persisted_event.session_id, set()))

        for queue in subscribers:
            await queue.put(persisted_event)

    async def replay(
        self,
        session_id: str,
        *,
        after_cursor: int | None = None,
        limit: int = 2_000,
    ) -> list[SessionEvent]:
        if self._session_factory is None:
            return []

        from app.db.repositories import SessionRepository

        with self._session_factory() as db_session:
            repository = SessionRepository(db_session)
            events = repository.list_session_events(
                session_id,
                after_cursor=after_cursor,
                limit=limit,
            )

        replay_events: list[SessionEvent] = []
        for event in events:
            try:
                event_type = SessionEventType(event.event_type)
            except ValueError:
                continue
            replay_events.append(
                SessionEvent(
                    type=event_type,
                    session_id=event.session_id,
                    cursor=event.cursor,
                    timestamp=event.timestamp,
                    payload=dict(event.payload_json),
                )
            )
        return replay_events

    def _persist_event(self, event: SessionEvent) -> SessionEvent:
        if self._session_factory is None:
            return event

        from app.db.repositories import SessionRepository

        try:
            with self._session_factory() as db_session:
                repository = SessionRepository(db_session)
                persisted = repository.create_session_event(
                    session_id=event.session_id,
                    event_type=event.type.value,
                    payload=dict(event.payload),
                    timestamp=event.timestamp,
                )
        except SQLAlchemyPoolTimeoutError:
            logger.warning(
                "Session event persistence skipped due DB pool timeout "
                "[session_id=%s event_type=%s]",
                event.session_id,
                event.type.value,
            )
            return event

        return SessionEvent(
            type=event.type,
            session_id=event.session_id,
            cursor=persisted.cursor,
            timestamp=persisted.timestamp,
            payload=dict(event.payload),
        )


event_broker = SessionEventBroker()


def get_event_broker() -> SessionEventBroker:
    return event_broker
