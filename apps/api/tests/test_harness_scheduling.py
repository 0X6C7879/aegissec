from __future__ import annotations

import asyncio
import importlib
from typing import Any

harness_messages = importlib.import_module("app.harness.messages")
QueryLoop = importlib.import_module("app.harness.query_loop").QueryLoop
tool_scheduling = importlib.import_module("app.harness.tool_scheduling")
ProviderTurnResult = harness_messages.ProviderTurnResult
QueryUsage = harness_messages.QueryUsage
ToolCallRequest = harness_messages.ToolCallRequest
ToolCallResult = harness_messages.ToolCallResult
MutatingTargetClass = importlib.import_module("app.harness.tools.base").MutatingTargetClass
ToolRiskLevel = importlib.import_module("app.harness.tools.base").ToolRiskLevel


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

    def dequeue_synthetic_tool_call(self) -> object | None:
        return None

    def build_synthetic_assistant_payload(self, tool_calls: list[object]) -> dict[str, object]:
        del tool_calls
        return {}

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


class _FakeDecision:
    allowed = True


class _FakeTool:
    def __init__(
        self,
        *,
        name: str,
        read_only: bool = False,
        risk_level: Any = ToolRiskLevel.LOW,
        mutating_target_class: Any = MutatingTargetClass.NONE,
    ) -> None:
        self.name = name
        self._read_only = read_only
        self._risk_level = risk_level
        self._mutating_target_class = mutating_target_class

    def is_read_only(self) -> bool:
        return self._read_only

    def risk_level(self) -> Any:
        return self._risk_level

    def mutating_target_class(self) -> Any:
        return self._mutating_target_class


class _FakePrepared:
    def __init__(self, tool: _FakeTool) -> None:
        self.tool = tool
        self.decision = _FakeDecision()


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


def test_build_tool_schedule_serializes_same_terminal_and_groups_distinct_terminals() -> None:
    detached_tool = _FakeTool(
        name="execute_terminal_command",
        risk_level=ToolRiskLevel.HIGH,
        mutating_target_class=MutatingTargetClass.RUNTIME,
    )
    tool_requests = [
        ToolCallRequest(
            tool_call_id="tool-1",
            tool_name="execute_terminal_command",
            arguments={"terminal_id": "term-a", "command": "pwd", "detach": True},
        ),
        ToolCallRequest(
            tool_call_id="tool-2",
            tool_name="execute_terminal_command",
            arguments={"terminal_id": "term-a", "command": "whoami", "detach": True},
        ),
        ToolCallRequest(
            tool_call_id="tool-3",
            tool_name="execute_terminal_command",
            arguments={"terminal_id": "term-b", "command": "id", "detach": True},
        ),
    ]

    phases = tool_scheduling.build_tool_schedule(
        tool_requests,
        [_FakePrepared(detached_tool) for _ in tool_requests],
    )

    assert len(phases) == 1
    assert phases[0].lane == "terminal_detached_parallel"
    grouped_ids = [
        [item.tool_request.tool_call_id for item in group]
        for group in tool_scheduling.build_parallel_groups(phases[0])
    ]
    assert grouped_ids == [["tool-1", "tool-2"], ["tool-3"]]


def test_build_tool_schedule_keeps_attached_terminal_and_legacy_runtime_serial() -> None:
    attached_terminal_tool = _FakeTool(
        name="execute_terminal_command",
        risk_level=ToolRiskLevel.HIGH,
        mutating_target_class=MutatingTargetClass.RUNTIME,
    )
    legacy_runtime_tool = _FakeTool(
        name="execute_kali_command",
        risk_level=ToolRiskLevel.HIGH,
        mutating_target_class=MutatingTargetClass.RUNTIME,
    )
    tool_requests = [
        ToolCallRequest(
            tool_call_id="tool-1",
            tool_name="execute_terminal_command",
            arguments={"terminal_id": "term-a", "command": "pwd", "detach": False},
        ),
        ToolCallRequest(
            tool_call_id="tool-2",
            tool_name="execute_kali_command",
            arguments={"command": "pwd"},
        ),
    ]

    phases = tool_scheduling.build_tool_schedule(
        tool_requests,
        [_FakePrepared(attached_terminal_tool), _FakePrepared(legacy_runtime_tool)],
    )

    assert [phase.lane for phase in phases] == ["serial_high_risk", "serial_high_risk"]
