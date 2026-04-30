from __future__ import annotations

import asyncio
import logging
from asyncio import QueueEmpty, QueueFull
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
    TERMINAL_JOB_STARTED = "terminal.job.started"
    TERMINAL_JOB_COMPLETED = "terminal.job.completed"
    TERMINAL_JOB_FAILED = "terminal.job.failed"
    TERMINAL_JOB_CANCELLED = "terminal.job.cancelled"
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


_MESSAGE_PERSIST_BASE_KEYS = (
    "session_id",
    "parent_message_id",
    "branch_id",
    "generation_id",
    "status",
    "message_kind",
    "sequence",
    "turn_index",
    "edited_from_message_id",
    "version_group_id",
    "created_at",
    "completed_at",
    "error_message",
)

_GRAPH_PERSIST_KEYS = (
    "run_id",
    "graph_type",
    "current_stage",
    "message_id",
    "assistant_message_id",
    "generation_id",
    "evidence_ids",
    "hypothesis_ids",
    "artifacts",
    "reason",
)

_DROPPABLE_EVENT_TYPES = frozenset(
    {
        SessionEventType.MESSAGE_DELTA,
        SessionEventType.MESSAGE_UPDATED,
        SessionEventType.ASSISTANT_TRACE,
        SessionEventType.GRAPH_UPDATED,
    }
)

_CRITICAL_EVENT_TYPES = frozenset(
    {
        SessionEventType.MESSAGE_COMPLETED,
        SessionEventType.GENERATION_FAILED,
        SessionEventType.GENERATION_CANCELLED,
        SessionEventType.SESSION_UPDATED,
        SessionEventType.SESSION_DELETED,
        SessionEventType.SESSION_RESTORED,
    }
)


def _read_non_empty_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value:
        return value
    return None


def _build_thin_message_payload(
    payload: dict[str, Any],
    *,
    keep_assistant_content: bool,
) -> dict[str, Any]:
    thin_payload: dict[str, Any] = {"__thin_replay": True}

    message_id = _read_non_empty_string(payload, "message_id") or _read_non_empty_string(
        payload, "id"
    )
    if message_id is not None:
        thin_payload["message_id"] = message_id
        thin_payload["id"] = message_id

    role = _read_non_empty_string(payload, "role")
    if role is not None:
        thin_payload["role"] = role

    for key in _MESSAGE_PERSIST_BASE_KEYS:
        if key in payload:
            thin_payload[key] = payload[key]

    content = payload.get("content")
    if isinstance(content, str):
        if role == "assistant" and not keep_assistant_content:
            thin_payload["content"] = ""
            thin_payload["content_length"] = len(content)
        else:
            thin_payload["content"] = content
    else:
        thin_payload["content"] = ""

    delta = payload.get("delta")
    if isinstance(delta, str):
        if role == "assistant" and not keep_assistant_content:
            thin_payload["delta_length"] = len(delta)
        else:
            thin_payload["delta"] = delta

    assistant_transcript = payload.get("assistant_transcript")
    if isinstance(assistant_transcript, list):
        thin_payload["assistant_transcript_count"] = len(assistant_transcript)

    attachments = payload.get("attachments")
    if isinstance(attachments, list):
        thin_payload["attachments_count"] = len(attachments)

    return thin_payload


def _build_thin_tool_finished_payload(payload: dict[str, Any]) -> dict[str, Any]:
    thin_payload = dict(payload)
    stdout = thin_payload.pop("stdout", None)
    stderr = thin_payload.pop("stderr", None)
    removed_result = thin_payload.pop("result", None)

    for key in ("output", "payload", "data", "graph_updates", "swarm_notifications"):
        thin_payload.pop(key, None)

    if "semantic_state" in thin_payload:
        thin_payload.pop("semantic_state", None)
        thin_payload["semantic_state_omitted"] = True

    if isinstance(stdout, str):
        thin_payload["stdout_length"] = len(stdout)
    if isinstance(stderr, str):
        thin_payload["stderr_length"] = len(stderr)
    if removed_result is not None:
        thin_payload["result_omitted"] = True

    thin_payload["__thin_replay"] = True
    return thin_payload


def _build_thin_assistant_trace_payload(payload: dict[str, Any]) -> dict[str, Any]:
    thin_payload = dict(payload)
    if "semantic_state" in thin_payload:
        thin_payload.pop("semantic_state", None)
        thin_payload["semantic_state_omitted"] = True
    thin_payload["__thin_replay"] = True
    return thin_payload


def _build_thin_graph_updated_payload(payload: dict[str, Any]) -> dict[str, Any]:
    thin_payload = {key: payload[key] for key in _GRAPH_PERSIST_KEYS if key in payload}
    graph_updates = payload.get("graph_updates")
    if isinstance(graph_updates, list):
        thin_payload["graph_updates_count"] = len(graph_updates)
    thin_payload["__thin_replay"] = True
    return thin_payload


def _build_persisted_payload(
    event_type: SessionEventType,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if event_type in {SessionEventType.MESSAGE_UPDATED, SessionEventType.MESSAGE_DELTA}:
        return _build_thin_message_payload(payload, keep_assistant_content=False)

    if event_type == SessionEventType.MESSAGE_COMPLETED:
        return _build_thin_message_payload(payload, keep_assistant_content=True)

    if event_type == SessionEventType.TOOL_CALL_FINISHED:
        return _build_thin_tool_finished_payload(payload)

    if event_type == SessionEventType.ASSISTANT_TRACE:
        return _build_thin_assistant_trace_payload(payload)

    if event_type == SessionEventType.GRAPH_UPDATED:
        return _build_thin_graph_updated_payload(payload)

    return dict(payload)


class SessionEventBroker:
    def __init__(
        self,
        *,
        subscriber_queue_maxsize: int = 512,
        publish_timeout_seconds: float = 0.05,
    ) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[SessionEvent]]] = {}
        self._lock = asyncio.Lock()
        self._session_factory: Callable[[], DBSession] | None = None
        self._subscriber_queue_maxsize = max(1, subscriber_queue_maxsize)
        self._publish_timeout_seconds = max(0.001, publish_timeout_seconds)

    def configure_persistence(self, session_factory: Callable[[], DBSession]) -> None:
        self._session_factory = session_factory

    async def subscribe(self, session_id: str) -> asyncio.Queue[SessionEvent]:
        queue: asyncio.Queue[SessionEvent] = asyncio.Queue(maxsize=self._subscriber_queue_maxsize)
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
            if self._enqueue_nowait(queue, persisted_event):
                continue

            if self._is_droppable_event(persisted_event):
                continue

            if not self._is_critical_event(persisted_event):
                continue

            if self._evict_oldest_droppable_event(queue) and self._enqueue_nowait(
                queue, persisted_event
            ):
                continue

            try:
                await asyncio.wait_for(
                    queue.put(persisted_event),
                    timeout=self._publish_timeout_seconds,
                )
            except TimeoutError:
                logger.warning(
                    "Dropping event for slow subscriber queue [session_id=%s event_type=%s]",
                    persisted_event.session_id,
                    persisted_event.type.value,
                )

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

        persisted_payload = _build_persisted_payload(event.type, event.payload)

        try:
            with self._session_factory() as db_session:
                repository = SessionRepository(db_session)
                persisted = repository.create_session_event(
                    session_id=event.session_id,
                    event_type=event.type.value,
                    payload=persisted_payload,
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

    @staticmethod
    def _is_droppable_event(event: SessionEvent) -> bool:
        return event.type in _DROPPABLE_EVENT_TYPES

    @staticmethod
    def _is_critical_event(event: SessionEvent) -> bool:
        return event.type in _CRITICAL_EVENT_TYPES

    @staticmethod
    def _enqueue_nowait(queue: asyncio.Queue[SessionEvent], event: SessionEvent) -> bool:
        try:
            queue.put_nowait(event)
        except QueueFull:
            return False
        return True

    @staticmethod
    def _evict_oldest_droppable_event(queue: asyncio.Queue[SessionEvent]) -> bool:
        buffered_events: list[SessionEvent] = []
        evicted = False
        while True:
            try:
                queued_event = queue.get_nowait()
            except QueueEmpty:
                break
            if not evicted and queued_event.type in _DROPPABLE_EVENT_TYPES:
                evicted = True
                continue
            buffered_events.append(queued_event)

        for queued_event in buffered_events:
            queue.put_nowait(queued_event)
        return evicted


event_broker = SessionEventBroker()


def get_event_broker() -> SessionEventBroker:
    return event_broker
