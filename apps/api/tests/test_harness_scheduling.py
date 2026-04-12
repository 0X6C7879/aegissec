from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from typing import Any, cast

import pytest

harness_messages = importlib.import_module("app.harness.messages")
QueryLoop = importlib.import_module("app.harness.query_loop").QueryLoop
tool_scheduling = importlib.import_module("app.harness.tool_scheduling")
ProviderTurnResult = harness_messages.ProviderTurnResult
QueryUsage = harness_messages.QueryUsage
ToolCallRequest = harness_messages.ToolCallRequest
ToolCallResult = harness_messages.ToolCallResult
MutatingTargetClass = importlib.import_module("app.harness.tools.base").MutatingTargetClass
ToolRiskLevel = importlib.import_module("app.harness.tools.base").ToolRiskLevel
ToolRuntimeLifecycleRunner = importlib.import_module(
    "app.harness.tool_runtime_runner"
).ToolRuntimeLifecycleRunner
ChatRuntimeError = importlib.import_module("app.services.chat_runtime").ChatRuntimeError


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


def test_build_tool_runtime_excludes_terminal_tools_for_swarm() -> None:
    class _SkillService:
        def list_loaded_skills_for_agent(self, *, session_id: str) -> list[Any]:
            del session_id
            return []

    runtime = importlib.import_module("app.harness.executor").build_tool_runtime(
        skill_service=_SkillService(),
        session_id="session-1",
        include_swarm_tools=True,
    )

    assert runtime.tool_registry.get("create_terminal_session") is None
    assert runtime.tool_registry.get("list_terminal_sessions") is None
    assert runtime.tool_registry.get("execute_terminal_command") is None
    assert runtime.tool_registry.get("read_terminal_buffer") is None
    assert runtime.tool_registry.get("stop_terminal_job") is None
    assert runtime.tool_registry.get("execute_kali_command") is not None


@pytest.mark.anyio
async def test_execute_constrained_parallel_phase_persists_success_before_failure() -> None:
    persisted_orders: list[int] = []
    failed_ids: list[str] = []

    async def publish_assistant_trace(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    lifecycle = ToolRuntimeLifecycleRunner(
        session=cast(Any, SimpleNamespace(id="session-1")),
        assistant_message=cast(Any, SimpleNamespace(id="assistant-1", generation_id=None)),
        repository=cast(Any, SimpleNamespace()),
        event_broker=cast(Any, SimpleNamespace()),
        session_state=None,
        publish_assistant_trace=publish_assistant_trace,
    )

    async def fake_publish_tool_started(*args: Any, **kwargs: Any) -> None:
        del args, kwargs

    async def fake_persist_tool_success(
        tool_request: Any,
        *,
        tool_result: Any,
        started_payload: dict[str, Any],
    ) -> Any:
        del tool_result, started_payload
        persisted_orders.append(int(tool_request.tool_call_id.split("-")[1]))
        return ToolCallResult(tool_name=tool_request.tool_name, payload={})

    async def fake_publish_tool_failed(
        tool_request: Any,
        *,
        started_payload: dict[str, Any],
        error_message: str,
        error_artifacts: Any,
    ) -> None:
        del started_payload, error_message, error_artifacts
        failed_ids.append(tool_request.tool_call_id)

    lifecycle.publish_tool_started = fake_publish_tool_started
    lifecycle.persist_tool_success = fake_persist_tool_success
    lifecycle.publish_tool_failed = fake_publish_tool_failed

    tool_requests = [
        ToolCallRequest(tool_call_id="tool-1", tool_name="execute_terminal_command", arguments={}),
        ToolCallRequest(tool_call_id="tool-2", tool_name="execute_terminal_command", arguments={}),
    ]
    prepared_items = [
        SimpleNamespace(
            order=index,
            tool_request=tool_request,
            prepared=SimpleNamespace(
                started_payload={},
                tool_call_metadata={},
                governance_metadata=None,
                trace_entry={},
            ),
        )
        for index, tool_request in enumerate(tool_requests)
    ]
    phase = SimpleNamespace(lane="terminal_detached_parallel", items=prepared_items)

    async def apply_pre_tool_hooks(*, runtime: Any, prepared: Any, tool_request: Any) -> Any:
        del runtime, tool_request
        return prepared

    async def run_tool_with_hooks(*, runtime: Any, prepared: Any, tool_request: Any) -> Any:
        del runtime, prepared
        if tool_request.tool_call_id == "tool-2":
            raise RuntimeError("boom")
        return {"status": "ok"}

    async def notify_tool_execution_error(
        *, runtime: Any, prepared: Any, tool_request: Any, error: Exception
    ) -> dict[str, Any]:
        del runtime, prepared, tool_request, error
        return {}

    executor_module = SimpleNamespace(
        apply_pre_tool_hooks=apply_pre_tool_hooks,
        run_tool_with_hooks=run_tool_with_hooks,
        notify_tool_execution_error=notify_tool_execution_error,
    )
    scheduling_module = SimpleNamespace(
        build_parallel_groups=lambda _: [[prepared_items[0]], [prepared_items[1]]]
    )

    with pytest.raises(ChatRuntimeError, match="boom"):
        await lifecycle.execute_constrained_parallel_phase(
            phase=phase,
            runtime=SimpleNamespace(),
            executor_module=executor_module,
            scheduling_module=scheduling_module,
        )

    assert persisted_orders == [1]
    assert failed_ids == ["tool-2"]
