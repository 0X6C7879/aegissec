from __future__ import annotations

import asyncio
from datetime import datetime
from enum import Enum
from typing import Any

from sqlmodel import Field, SQLModel

from app.db.models import utc_now


class SessionEventType(str, Enum):
    SESSION_CREATED = "session.created"
    SESSION_UPDATED = "session.updated"
    SESSION_DELETED = "session.deleted"
    SESSION_RESTORED = "session.restored"
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
    timestamp: datetime = Field(default_factory=utc_now)
    payload: dict[str, Any]


class SessionEventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[SessionEvent]]] = {}
        self._lock = asyncio.Lock()

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
        async with self._lock:
            subscribers = list(self._subscribers.get(event.session_id, set()))

        for queue in subscribers:
            await queue.put(event)


event_broker = SessionEventBroker()


def get_event_broker() -> SessionEventBroker:
    return event_broker
