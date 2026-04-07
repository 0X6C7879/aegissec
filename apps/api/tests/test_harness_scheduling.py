from __future__ import annotations

import asyncio
import importlib
from typing import Any

harness_messages = importlib.import_module("app.harness.messages")
QueryLoop = importlib.import_module("app.harness.query_loop").QueryLoop
ProviderTurnResult = harness_messages.ProviderTurnResult
QueryUsage = harness_messages.QueryUsage
ToolCallRequest = harness_messages.ToolCallRequest
ToolCallResult = harness_messages.ToolCallResult


class _FakeEngine:
    def __init__(self) -> None:
        self.usage = QueryUsage()
        self.pending_continuation = False
        self._turn_index = 0
        self.appended_results: list[list[str]] = []
        self.compact_calls = 0

    async def request_turn(self, *, allow_tools: bool, callbacks: Any | None = None) -> Any:
        del allow_tools, callbacks
        self._turn_index += 1
        if self._turn_index == 1:
            return ProviderTurnResult(
                assistant_payload={},
                text_content=None,
                tool_calls=[
                    ToolCallRequest(
                        tool_call_id="tool-1",
                        tool_name="list_available_skills",
                        arguments={},
                    ),
                    ToolCallRequest(
                        tool_call_id="tool-2",
                        tool_name="read_skill_content",
                        arguments={"skill_name_or_id": "demo"},
                    ),
                ],
            )
        return ProviderTurnResult(assistant_payload={}, text_content="done", tool_calls=[])

    def append_tool_results(
        self,
        *,
        assistant_payload: dict[str, object],
        tool_calls: list[object],
        tool_results: list[object],
    ) -> None:
        del assistant_payload
        del tool_results
        self.appended_results.append(
            [str(getattr(tool_call, "tool_call_id")) for tool_call in tool_calls]
        )

    def maybe_auto_compact(self) -> None:
        self.compact_calls += 1

    async def generate_tool_budget_reply(self, callbacks: Any | None = None) -> str:
        del callbacks
        return "budget"


class _BatchExecutor:
    def __init__(self) -> None:
        self.batch_calls: list[list[str]] = []
        self.single_calls: list[str] = []

    async def __call__(self, tool_request: object) -> Any:
        if hasattr(tool_request, "tool_call_id"):
            self.single_calls.append(str(getattr(tool_request, "tool_call_id")))
        raise AssertionError("single-tool execution path should not be used")

    async def __batch_execute__(self, tool_requests: list[object]) -> list[Any]:
        requests = [
            request
            for request in tool_requests
            if hasattr(request, "tool_call_id") and hasattr(request, "tool_name")
        ]
        self.batch_calls.append([str(getattr(request, "tool_call_id")) for request in requests])
        return [
            ToolCallResult(
                tool_name=str(getattr(request, "tool_name")),
                payload={"tool_call_id": str(getattr(request, "tool_call_id"))},
            )
            for request in requests
        ]


def test_query_loop_uses_batch_executor_for_tool_rounds() -> None:
    engine = _FakeEngine()
    executor = _BatchExecutor()

    result = asyncio.run(QueryLoop(max_turns=4).run(engine, execute_tool=executor, callbacks=None))

    assert result == "done"
    assert executor.batch_calls == [["tool-1", "tool-2"]]
    assert executor.single_calls == []
    assert engine.appended_results == [["tool-1", "tool-2"]]
    assert engine.compact_calls == 1
    assert engine.usage.model_turns == 2
    assert engine.usage.tool_rounds == 1
    assert engine.usage.tool_calls == 2
