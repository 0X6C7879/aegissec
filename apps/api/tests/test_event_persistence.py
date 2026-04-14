from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from pytest import MonkeyPatch

import app.db.repositories as repositories_module
from app.core.events import (
    SessionEvent,
    SessionEventBroker,
    SessionEventType,
    _build_persisted_payload,
)


def test_build_persisted_payload_thins_assistant_message_updates() -> None:
    payload: dict[str, Any] = {
        "id": "msg-1",
        "message_id": "msg-1",
        "session_id": "session-1",
        "generation_id": "gen-1",
        "role": "assistant",
        "status": "streaming",
        "content": "这是一段会不断增长的输出内容。",
        "delta": "新增片段",
        "assistant_transcript": [{"id": "segment-1"}],
        "attachments": [{"id": "attachment-1"}],
        "metadata": {"large": "payload"},
    }

    persisted = _build_persisted_payload(SessionEventType.MESSAGE_UPDATED, payload)

    assert persisted["__thin_replay"] is True
    assert persisted["id"] == "msg-1"
    assert persisted["message_id"] == "msg-1"
    assert persisted["role"] == "assistant"
    assert persisted["content"] == ""
    assert persisted["content_length"] == len(payload["content"])
    assert persisted["delta_length"] == len(payload["delta"])
    assert persisted["assistant_transcript_count"] == 1
    assert persisted["attachments_count"] == 1
    assert "assistant_transcript" not in persisted
    assert "metadata" not in persisted


def test_build_persisted_payload_keeps_user_message_content() -> None:
    payload: dict[str, Any] = {
        "id": "msg-user-1",
        "session_id": "session-1",
        "role": "user",
        "content": "完整用户输入",
        "delta": "unused",
    }

    persisted = _build_persisted_payload(SessionEventType.MESSAGE_UPDATED, payload)

    assert persisted["content"] == "完整用户输入"
    assert persisted["delta"] == "unused"
    assert "content_length" not in persisted
    assert "delta_length" not in persisted


def test_build_persisted_payload_thins_tool_finished_payload() -> None:
    payload: dict[str, Any] = {
        "tool": "execute_kali_command",
        "tool_call_id": "tool-call-1",
        "status": "success",
        "command": "ls -la",
        "stdout": "line-1\nline-2",
        "stderr": "",
        "result": {"stdout": "line-1\nline-2", "stderr": ""},
        "graph_updates": [{"stable_key": "runtime:1"}],
        "semantic_state": {"evidence_ids": ["runtime:1"]},
    }

    persisted = _build_persisted_payload(SessionEventType.TOOL_CALL_FINISHED, payload)

    assert persisted["__thin_replay"] is True
    assert persisted["tool_call_id"] == "tool-call-1"
    assert persisted["status"] == "success"
    assert persisted["command"] == "ls -la"
    assert persisted["stdout_length"] == len("line-1\nline-2")
    assert persisted["stderr_length"] == 0
    assert persisted["result_omitted"] is True
    assert persisted["semantic_state_omitted"] is True
    assert "stdout" not in persisted
    assert "stderr" not in persisted
    assert "result" not in persisted
    assert "graph_updates" not in persisted


def test_publish_persists_thin_payload_but_keeps_live_payload(monkeypatch: MonkeyPatch) -> None:
    captured_payload: dict[str, Any] = {}

    class _DummySessionContext:
        def __enter__(self) -> object:
            return object()

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            tb: object | None,
        ) -> None:
            del exc_type, exc, tb
            return None

    class _DummySessionFactory:
        def __call__(self) -> _DummySessionContext:
            return _DummySessionContext()

    class _DummyRepository:
        def __init__(self, db_session: object) -> None:
            del db_session

        def create_session_event(
            self,
            *,
            session_id: str,
            event_type: str,
            payload: dict[str, object],
            timestamp: datetime | None = None,
        ) -> SimpleNamespace:
            del session_id, event_type
            captured_payload.update(payload)
            return SimpleNamespace(cursor=17, timestamp=timestamp or datetime.now(UTC))

    monkeypatch.setattr(repositories_module, "SessionRepository", _DummyRepository)

    broker = SessionEventBroker()
    broker.configure_persistence(_DummySessionFactory())
    event = SessionEvent(
        type=SessionEventType.MESSAGE_UPDATED,
        session_id="session-1",
        payload={
            "id": "msg-1",
            "message_id": "msg-1",
            "session_id": "session-1",
            "role": "assistant",
            "content": "live content should stay intact",
        },
    )

    async def _publish_and_read() -> SessionEvent:
        queue = await broker.subscribe("session-1")
        await broker.publish(event)
        published = await queue.get()
        await broker.unsubscribe("session-1", queue)
        return published

    published_event = asyncio.run(_publish_and_read())

    assert published_event.cursor == 17
    assert published_event.payload["content"] == "live content should stay intact"
    assert captured_payload["__thin_replay"] is True
    assert captured_payload["content"] == ""
    assert captured_payload["content_length"] == len("live content should stay intact")
