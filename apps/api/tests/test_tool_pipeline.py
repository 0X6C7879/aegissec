from __future__ import annotations

import pytest

from app.agent.tool_registry import (
    NoOpToolExecutionHooks,
    ToolCapability,
    ToolCategory,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolPolicyDecision,
    ToolRegistry,
    ToolSpec,
)
from app.agent.tool_runtime_models import (
    ToolExecutionError,
    ToolInterruptBehavior,
    ToolValidationError,
)
from app.agent.workflow import WorkflowExecutionContext
from app.db.models import TaskNode, TaskNodeStatus, TaskNodeType


def _build_context() -> WorkflowExecutionContext:
    return WorkflowExecutionContext(
        session_id="session-1",
        workflow_run_id="run-1",
        goal="test tool pipeline",
        template_name="authorized-assessment",
        current_stage="analysis",
        runtime_policy={},
    )


def _build_task(name: str = "tool.task") -> TaskNode:
    return TaskNode(
        workflow_run_id="run-1",
        name=name,
        node_type=TaskNodeType.TASK,
        status=TaskNodeStatus.READY,
        sequence=1,
        metadata_json={"stage_key": "analysis"},
    )


def test_tool_pipeline_returns_failed_result_for_schema_validation_failure() -> None:
    registry = ToolRegistry(hooks=NoOpToolExecutionHooks())
    called = {"value": False}
    spec = ToolSpec(
        name="workflow.schema_failure",
        category=ToolCategory.DISCOVERY,
        capability=ToolCapability.CAPABILITY_SNAPSHOT,
        input_schema={
            "type": "object",
            "properties": {"session_id": {"type": "integer"}},
        },
    )
    registry.register(
        spec=spec,
        matcher=lambda _task: True,
        handler=lambda request: _unexpected_handler(request, called),
    )

    envelope = registry.execute_envelope(context=_build_context(), task=_build_task())

    assert called["value"] is False
    assert envelope.runtime_result.status is TaskNodeStatus.FAILED
    assert envelope.runtime_result.output_payload["validation_failed"] is True
    assert envelope.runtime_result.output_payload["validation_stage"] == "schema_validate"


def test_tool_pipeline_returns_failed_result_for_semantic_validation_failure() -> None:
    registry = ToolRegistry(hooks=NoOpToolExecutionHooks())
    called = {"value": False}
    spec = ToolSpec(
        name="workflow.semantic_failure",
        category=ToolCategory.DISCOVERY,
        capability=ToolCapability.CAPABILITY_SNAPSHOT,
        input_validator=lambda _payload, _request: _raise_validation_error(),
    )
    registry.register(
        spec=spec,
        matcher=lambda _task: True,
        handler=lambda request: _unexpected_handler(request, called),
    )

    envelope = registry.execute_envelope(context=_build_context(), task=_build_task())

    assert called["value"] is False
    assert envelope.runtime_result.status is TaskNodeStatus.FAILED
    assert envelope.runtime_result.output_payload["validation_failed"] is True
    assert envelope.runtime_result.output_payload["validation_stage"] == "semantic_validate"


def test_tool_pipeline_returns_failed_result_for_output_schema_validation_failure() -> None:
    registry = ToolRegistry(hooks=NoOpToolExecutionHooks())
    spec = ToolSpec(
        name="workflow.output_schema_failure",
        category=ToolCategory.DISCOVERY,
        capability=ToolCapability.CAPABILITY_SNAPSHOT,
        output_schema={
            "type": "object",
            "required": ["stdout"],
            "properties": {"stdout": {"type": "integer"}},
        },
    )

    def handler(request: ToolExecutionRequest) -> ToolExecutionResult:
        return ToolExecutionResult(
            spec=spec,
            source_type="runtime",
            source_name="test",
            command_or_action=f"execute:{request.task.name}",
            input_payload={"trace_id": request.trace_id},
            output_payload={"stdout": "not-an-integer", "stderr": "", "exit_code": 0},
            status=TaskNodeStatus.COMPLETED,
            started_at=request.started_at,
            ended_at=request.started_at,
        )

    registry.register(spec=spec, matcher=lambda _task: True, handler=handler)

    envelope = registry.execute_envelope(context=_build_context(), task=_build_task())

    assert envelope.runtime_result.status is TaskNodeStatus.FAILED
    assert envelope.runtime_result.output_payload["validation_failed"] is True
    assert envelope.runtime_result.output_payload["validation_stage"] == "standardize_result"


def test_tool_pipeline_preserves_policy_denial_as_failed_result() -> None:
    class DenyAllPolicy:
        def evaluate(self, *, request: ToolExecutionRequest, spec: ToolSpec) -> ToolPolicyDecision:
            del request, spec
            return ToolPolicyDecision.deny("blocked by policy", metadata={"allow_write": False})

    registry = ToolRegistry(policy=DenyAllPolicy(), hooks=NoOpToolExecutionHooks())
    spec = ToolSpec(
        name="workflow.policy_denied",
        category=ToolCategory.EXECUTION,
        capability=ToolCapability.STRUCTURED_RUNTIME,
    )
    registry.register(
        spec=spec,
        matcher=lambda _task: True,
        handler=lambda request: (_ for _ in ()).throw(AssertionError(request.task.name)),
    )

    envelope = registry.execute_envelope(context=_build_context(), task=_build_task())

    assert envelope.policy_decision is not None
    assert envelope.policy_decision.allowed is False
    assert envelope.runtime_result.status is TaskNodeStatus.FAILED
    assert envelope.runtime_result.output_payload["policy_denied"] is True
    assert envelope.runtime_result.output_payload["policy_reason"] == "blocked by policy"
    runtime_protocol = envelope.runtime_result.output_payload["runtime_protocol"]
    assert isinstance(runtime_protocol, dict)
    assert runtime_protocol["policy"] == {
        "allowed": False,
        "reason": "blocked by policy",
        "metadata": {"allow_write": False},
    }


def test_tool_pipeline_raises_standard_execution_error_and_calls_error_hook() -> None:
    class RecordingHooks:
        def __init__(self) -> None:
            self.events: list[str] = []

        def before_execution(self, *, request: ToolExecutionRequest, spec: ToolSpec) -> None:
            del request, spec
            self.events.append("before")

        def after_execution(
            self, *, request: ToolExecutionRequest, result: ToolExecutionResult
        ) -> None:
            del request, result
            self.events.append("after")

        def on_execution_error(
            self, *, request: ToolExecutionRequest, spec: ToolSpec, error: Exception
        ) -> None:
            del request, spec
            self.events.append(f"error:{error}")

    hooks = RecordingHooks()
    registry = ToolRegistry(hooks=hooks)
    spec = ToolSpec(
        name="workflow.execution_error",
        category=ToolCategory.EXECUTION,
        capability=ToolCapability.STRUCTURED_RUNTIME,
    )
    registry.register(
        spec=spec,
        matcher=lambda _task: True,
        handler=lambda _request: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with pytest.raises(ToolExecutionError, match="boom"):
        registry.execute_envelope(context=_build_context(), task=_build_task())

    assert hooks.events == ["before", "error:boom"]


def test_tool_pipeline_renders_transcript_blocks_with_metadata_only_protocol_block() -> None:
    registry = ToolRegistry(hooks=NoOpToolExecutionHooks())
    spec = ToolSpec(
        name="workflow.transcript",
        category=ToolCategory.DISCOVERY,
        capability=ToolCapability.CAPABILITY_SNAPSHOT,
        use_message_renderer=lambda request, _payload: f"use:{request.task.name}",
        result_message_renderer=lambda request, _input, output: (
            f"result:{request.task.name}:{output['stdout']}"
        ),
    )

    def handler(request: ToolExecutionRequest) -> ToolExecutionResult:
        return ToolExecutionResult(
            spec=spec,
            source_type="runtime",
            source_name="test",
            command_or_action=f"execute:{request.task.name}",
            input_payload={"trace_id": request.trace_id},
            output_payload={"stdout": "ok", "stderr": "", "exit_code": 0},
            status=TaskNodeStatus.COMPLETED,
            started_at=request.started_at,
            ended_at=request.started_at,
        )

    registry.register(spec=spec, matcher=lambda _task: True, handler=handler)

    envelope = registry.execute_envelope(
        context=_build_context(), task=_build_task("transcript.task")
    )

    assert [block.kind for block in envelope.transcript_blocks] == [
        "tool_use",
        "tool_result",
        "tool_protocol",
    ]
    assert envelope.transcript_blocks[0].content == "use:transcript.task"
    assert envelope.transcript_blocks[1].content == "result:transcript.task:ok"
    assert envelope.transcript_blocks[2].is_metadata_only is True
    assert envelope.runtime_protocol["interrupt_behavior"] == "none"


def test_tool_pipeline_blocks_execution_for_required_user_interaction() -> None:
    registry = ToolRegistry(hooks=NoOpToolExecutionHooks())
    called = {"value": False}
    spec = ToolSpec(
        name="workflow.user_interaction",
        category=ToolCategory.EXECUTION,
        capability=ToolCapability.STRUCTURED_RUNTIME,
        interaction_required=True,
        interrupt_behavior_value=ToolInterruptBehavior.USER_INTERACTION,
    )
    registry.register(
        spec=spec,
        matcher=lambda _task: True,
        handler=lambda request: _unexpected_handler(request, called),
    )

    envelope = registry.execute_envelope(context=_build_context(), task=_build_task("needs.input"))

    assert called["value"] is False
    assert envelope.runtime_result.status is TaskNodeStatus.BLOCKED
    assert envelope.runtime_result.output_payload["execution_blocked"] is True
    assert envelope.runtime_result.output_payload["interaction_required"] is True
    assert envelope.runtime_result.output_payload["interrupt_behavior"] == "user_interaction"
    assert envelope.transcript_blocks[1].kind == "tool_error"
    assert envelope.runtime_protocol["requires_user_interaction"] is True


def test_tool_pipeline_calls_hooks_in_stage_order() -> None:
    class RecordingHooks:
        def __init__(self) -> None:
            self.events: list[str] = []

        def before_execution(self, *, request: ToolExecutionRequest, spec: ToolSpec) -> None:
            del request, spec
            self.events.append("before")

        def after_execution(
            self, *, request: ToolExecutionRequest, result: ToolExecutionResult
        ) -> None:
            del request, result
            self.events.append("after")

        def on_execution_error(
            self, *, request: ToolExecutionRequest, spec: ToolSpec, error: Exception
        ) -> None:
            del request, spec, error
            self.events.append("error")

    hooks = RecordingHooks()
    registry = ToolRegistry(hooks=hooks)
    spec = ToolSpec(
        name="workflow.hook_order",
        category=ToolCategory.DISCOVERY,
        capability=ToolCapability.CAPABILITY_SNAPSHOT,
    )

    def handler(request: ToolExecutionRequest) -> ToolExecutionResult:
        hooks.events.append("execute")
        return ToolExecutionResult(
            spec=spec,
            source_type="runtime",
            source_name="test",
            command_or_action=f"execute:{request.task.name}",
            input_payload={"trace_id": request.trace_id},
            output_payload={"stdout": "ok", "stderr": "", "exit_code": 0},
            status=TaskNodeStatus.COMPLETED,
            started_at=request.started_at,
            ended_at=request.started_at,
        )

    registry.register(spec=spec, matcher=lambda _task: True, handler=handler)

    registry.execute_envelope(context=_build_context(), task=_build_task("hook.order"))

    assert hooks.events == ["before", "execute", "after"]


def _unexpected_handler(
    request: ToolExecutionRequest, called: dict[str, bool]
) -> ToolExecutionResult:
    called["value"] = True
    raise AssertionError(request.task.name)


def _raise_validation_error() -> None:
    raise ToolValidationError(
        "semantic validation failed",
        stage="semantic_validate",
        details={"reason": "invalid semantic input"},
    )
