from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

from app.db.models import AssistantTranscriptSegmentKind
from app.harness import tool_runtime_runner as runner_module
from app.harness.tool_runtime_runner import ToolRuntimeLifecycleRunner
from app.services.chat_runtime import ToolCallRequest


class _DummyEventBroker:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


async def _noop_async(*args: Any, **kwargs: Any) -> None:
    del args, kwargs


def test_persist_tool_success_skips_orphan_tool_result_transcript_segment(
    monkeypatch: Any,
) -> None:
    appended_segments: list[dict[str, Any]] = []

    monkeypatch.setattr(runner_module, "stage_semantic_deltas", lambda state, deltas: None)
    monkeypatch.setattr(
        runner_module, "stage_swarm_notification_semantics", lambda state, items: None
    )
    monkeypatch.setattr(runner_module, "drain_semantic_snapshot", lambda *args, **kwargs: {})
    monkeypatch.setattr(runner_module, "message_transcript_segments", lambda *args, **kwargs: [])
    monkeypatch.setattr(runner_module, "find_transcript_segment", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runner_module,
        "append_transcript_segment",
        lambda repository, **kwargs: appended_segments.append(kwargs),
    )
    monkeypatch.setattr(runner_module, "update_transcript_segment", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner_module, "publish_message_event", _noop_async)
    monkeypatch.setattr(runner_module, "publish_attack_graph_updated", _noop_async)
    monkeypatch.setattr(runner_module, "publish_swarm_notifications", _noop_async)

    lifecycle = ToolRuntimeLifecycleRunner(
        session=cast(Any, SimpleNamespace(id="session-1")),
        assistant_message=cast(Any, SimpleNamespace(generation_id=None)),
        repository=cast(Any, SimpleNamespace()),
        event_broker=cast(Any, _DummyEventBroker()),
        session_state=None,
        publish_assistant_trace=_noop_async,
    )

    tool_request = ToolCallRequest(
        tool_call_id="call-1",
        tool_name="execute_kali_command",
        arguments={"command": "pwd"},
    )
    tool_result = SimpleNamespace(
        semantic_deltas=[],
        event_payload={},
        transcript_result_metadata={},
        transcript_tool_call_metadata={},
        status="completed",
        payload={"stdout": "ok", "status": "completed"},
        trace_entry={},
        step_metadata={},
        safe_summary="命令已完成，状态：completed。",
    )

    result = asyncio.run(
        lifecycle.persist_tool_success(
            tool_request,
            tool_result=tool_result,
            started_payload={"tool": tool_request.tool_name},
        )
    )

    assert all(
        segment["kind"] != AssistantTranscriptSegmentKind.TOOL_RESULT
        for segment in appended_segments
    )
    assert result.tool_call_id == "call-1"
    assert result.safe_summary == "命令已完成，状态：completed。"
