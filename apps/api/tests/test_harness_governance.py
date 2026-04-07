import importlib
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel

governance_checker = importlib.import_module("app.harness.governance.checker")
harness_messages = importlib.import_module("app.harness.messages")
harness_tools_base = importlib.import_module("app.harness.tools.base")
harness_tools_registry = importlib.import_module("app.harness.tools.registry")

DefaultHarnessToolDecisionChecker = governance_checker.DefaultHarnessToolDecisionChecker
HarnessToolDecisionRequest = governance_checker.HarnessToolDecisionRequest
ToolCallRequest = harness_messages.ToolCallRequest
MutatingTargetClass = harness_tools_base.MutatingTargetClass
ToolExecutionContext = harness_tools_base.ToolExecutionContext
ToolRiskLevel = harness_tools_base.ToolRiskLevel
ToolHookRegistry = harness_tools_registry.ToolHookRegistry


class _EmptyInput(BaseModel):
    pass


class _RuntimeCommandTool:
    name = "execute_kali_command"
    description = "runtime command"
    input_model = _EmptyInput
    scope_sensitive = True
    evidence_effects = False

    def is_read_only(self) -> bool:
        return False

    def risk_level(self) -> Any:
        return ToolRiskLevel.HIGH

    def capability_tags(self) -> tuple[str, ...]:
        return ()

    def mutating_target_class(self) -> Any:
        return MutatingTargetClass.RUNTIME

    async def execute(
        self,
        context: Any,
        arguments: Mapping[str, Any],
    ) -> Any:
        del context, arguments
        raise AssertionError("execute should not be called in governance unit tests")


class _DestructiveTool:
    name = "wipe_runtime"
    description = "destructive runtime action"
    input_model = _EmptyInput
    scope_sensitive = False
    evidence_effects = False

    def is_read_only(self) -> bool:
        return False

    def risk_level(self) -> Any:
        return ToolRiskLevel.DESTRUCTIVE

    def capability_tags(self) -> tuple[str, ...]:
        return ()

    def mutating_target_class(self) -> Any:
        return MutatingTargetClass.RUNTIME

    async def execute(
        self,
        context: Any,
        arguments: Mapping[str, Any],
    ) -> Any:
        del context, arguments
        raise AssertionError("execute should not be called in governance unit tests")


def _build_context(runtime_policy_json: dict[str, Any] | None = None) -> Any:
    session = SimpleNamespace(runtime_policy_json=runtime_policy_json)
    return ToolExecutionContext(
        session=session,
        assistant_message=None,
        runtime_service=None,
        skill_service=None,
        mcp_service=None,
        available_skills=[],
    )


def test_default_checker_denies_write_command_when_runtime_policy_blocks_write() -> None:
    checker = DefaultHarnessToolDecisionChecker()
    tool = _RuntimeCommandTool()
    decision = checker.evaluate(
        HarnessToolDecisionRequest(
            tool_request=ToolCallRequest(
                tool_name="execute_kali_command",
                tool_call_id="call-1",
                arguments={"command": "touch reports/blocked.txt"},
            ),
            tool=tool,
            execution_context=_build_context(
                {
                    "allow_network": True,
                    "allow_write": False,
                    "max_execution_seconds": 300,
                    "max_command_length": 4000,
                }
            ),
        )
    )

    assert decision.action == "deny"
    assert decision.reason == "Runtime policy blocks write-capable commands."
    assert decision.metadata["risk_level"] == "high"
    assert decision.metadata["mutating_target_class"] == "runtime"


def test_default_checker_requires_approval_for_destructive_tool() -> None:
    checker = DefaultHarnessToolDecisionChecker()
    decision = checker.evaluate(
        HarnessToolDecisionRequest(
            tool_request=ToolCallRequest(
                tool_name="wipe_runtime",
                tool_call_id="call-2",
                arguments={},
            ),
            tool=_DestructiveTool(),
            execution_context=_build_context(),
        )
    )

    assert decision.action == "require_approval"
    assert decision.reason == "Destructive tools require explicit approval."


def test_tool_hook_registry_returns_registered_tool_hooks() -> None:
    hook_registry = ToolHookRegistry()
    hook = object()

    hook_registry.register_for_tool("execute_kali_command", hook)

    resolved_hooks = list(hook_registry.iter_hooks("execute_kali_command"))

    assert resolved_hooks == [hook]
