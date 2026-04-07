from __future__ import annotations

from typing import Any

from app.core.events import SessionEvent, SessionEventBroker, SessionEventType
from app.db.models import GraphType, Message, Session, to_message_read, to_session_read
from app.harness import events as harness_events


def message_payload(message: Message) -> dict[str, Any]:
    payload = to_message_read(message).model_dump(mode="json")
    payload["message_id"] = payload["id"]
    return payload


async def publish_session_updated(
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


async def publish_message_event(
    event_broker: SessionEventBroker,
    *,
    event_type: SessionEventType,
    session_id: str,
    message: Message,
    delta: str | None = None,
) -> None:
    payload = message_payload(message)
    if delta is not None:
        payload["delta"] = delta
    await event_broker.publish(
        SessionEvent(type=event_type, session_id=session_id, payload=payload)
    )


async def publish_generation_started(
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


async def publish_generation_cancelled(
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


async def publish_generation_failed(
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


async def publish_attack_graph_updated(
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    assistant_message: Message,
    semantic_snapshot: dict[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {
        "run_id": "",
        "graph_type": GraphType.ATTACK.value,
        "current_stage": None,
        "message_id": assistant_message.id,
        "assistant_message_id": assistant_message.id,
    }
    if assistant_message.generation_id is not None:
        payload["generation_id"] = assistant_message.generation_id
    payload.update(harness_events.semantic_event_payload(semantic_snapshot))
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.GRAPH_UPDATED,
            session_id=session_id,
            payload=payload,
        )
    )


async def publish_swarm_notifications(
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    notifications: list[dict[str, Any]],
) -> None:
    for notification in notifications:
        status = str(notification.get("status", "")).lower()
        event_type = harness_events.swarm_notification_to_event_type(status)
        await event_broker.publish(
            SessionEvent(
                type=event_type,
                session_id=session_id,
                payload={
                    "agent_id": notification.get("agent_id"),
                    "task_id": notification.get("task_id"),
                    "status": notification.get("status"),
                    "summary": notification.get("summary"),
                    "result": notification.get("result"),
                    "usage": notification.get("usage"),
                    "evidence_ids": notification.get("evidence_ids", []),
                    "hypothesis_ids": notification.get("hypothesis_ids", []),
                    "graph_updates": notification.get("graph_updates", []),
                    "artifacts": notification.get("artifacts", []),
                    "reason": notification.get("reason"),
                    "metadata": notification.get("metadata", {}),
                },
            )
        )
