from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from app.db.models import utc_now


class MailboxMessageKind(StrEnum):
    USER_MESSAGE = "user_message"
    SHUTDOWN = "shutdown"
    IDLE = "idle"
    SYSTEM = "system"


@dataclass(slots=True)
class MailboxMessage:
    message_id: str
    sender_id: str
    recipient_id: str
    kind: MailboxMessageKind
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)

    def as_payload(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "sender_id": self.sender_id,
            "recipient_id": self.recipient_id,
            "kind": self.kind.value,
            "payload": dict(self.payload),
            "created_at": self.created_at.isoformat(),
        }


class SwarmMailbox:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[MailboxMessage]] = {}

    def ensure_queue(self, agent_id: str) -> asyncio.Queue[MailboxMessage]:
        return self._queues.setdefault(agent_id, asyncio.Queue())

    async def send(self, message: MailboxMessage) -> MailboxMessage:
        await self.ensure_queue(message.recipient_id).put(message)
        return message

    async def send_to_agent(
        self,
        *,
        sender_id: str,
        recipient_id: str,
        kind: MailboxMessageKind,
        payload: dict[str, Any] | None = None,
    ) -> MailboxMessage:
        message = MailboxMessage(
            message_id=str(uuid4()),
            sender_id=sender_id,
            recipient_id=recipient_id,
            kind=kind,
            payload=dict(payload or {}),
        )
        return await self.send(message)

    async def receive(
        self,
        agent_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> MailboxMessage | None:
        queue = self.ensure_queue(agent_id)
        if timeout_seconds is None:
            return await queue.get()
        try:
            return await asyncio.wait_for(queue.get(), timeout=timeout_seconds)
        except TimeoutError:
            return None

    def drain(self, agent_id: str, *, limit: int = 100) -> list[MailboxMessage]:
        queue = self.ensure_queue(agent_id)
        drained: list[MailboxMessage] = []
        while len(drained) < limit and not queue.empty():
            drained.append(queue.get_nowait())
        return drained


def create_user_message(
    *, sender_id: str, recipient_id: str, content: str, metadata: dict[str, Any] | None = None
) -> MailboxMessage:
    return MailboxMessage(
        message_id=str(uuid4()),
        sender_id=sender_id,
        recipient_id=recipient_id,
        kind=MailboxMessageKind.USER_MESSAGE,
        payload={"content": content, "metadata": dict(metadata or {})},
    )


def create_shutdown_request(
    *, sender_id: str, recipient_id: str, reason: str | None = None
) -> MailboxMessage:
    return MailboxMessage(
        message_id=str(uuid4()),
        sender_id=sender_id,
        recipient_id=recipient_id,
        kind=MailboxMessageKind.SHUTDOWN,
        payload={"reason": reason or "shutdown_requested"},
    )


def create_idle_notification(*, sender_id: str, recipient_id: str) -> MailboxMessage:
    return MailboxMessage(
        message_id=str(uuid4()),
        sender_id=sender_id,
        recipient_id=recipient_id,
        kind=MailboxMessageKind.IDLE,
        payload={},
    )
