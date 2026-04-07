from __future__ import annotations

import asyncio
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.harness.executor import (
    HarnessToolRuntime,
    apply_pre_tool_hooks,
    notify_tool_execution_error,
    prepare_tool_execution,
    run_tool_with_hooks,
)
from app.harness.messages import ChatRuntimeError, ToolCallRequest
from app.harness.tools.base import BaseTool, MutatingTargetClass, ToolResult, ToolRiskLevel
from app.harness.tools.defaults import build_default_tool_hook_registry
from app.harness.tools.registry import ToolRegistry


class _DummyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)


class _DummyTool(BaseTool[_DummyInput]):
    name = "dummy_tool"
    description = "Dummy tool for hook testing."
    input_model = _DummyInput

    def is_read_only(self) -> bool:
        return True

    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.LOW

    def capability_tags(self) -> tuple[str, ...]:
        return ("runtime", "evidence")

    def mutating_target_class(self) -> MutatingTargetClass:
        return MutatingTargetClass.NONE

    async def execute(self, context: Any, arguments: Mapping[str, Any]) -> ToolResult:
        del context
        parsed = self.parse_arguments(arguments)
        return ToolResult(
            tool_name=self.name,
            payload={"command": parsed.command, "ok": True},
            status="completed",
            safe_summary="dummy execution completed",
            transcript_result_metadata={"result": {"ok": True}},
            event_payload={
                "evidence_ids": ["ev-1"],
                "hypothesis_ids": ["hyp-1"],
                "graph_updates": [{"kind": "node", "id": "n-1"}],
                "artifacts": ["artifact-1"],
                "reason": "new evidence ingested",
            },
            trace_entry={"tool_result": "ok"},
        )


class _DecisionChecker:
    def evaluate(self, request: Any) -> Any:
        del request
        return SimpleNamespace(
            allowed=True,
            action="allow",
            reason=None,
            metadata={
                "risk_level": "low",
                "mutating_target_class": "none",
                "capability_tags": ["runtime", "evidence"],
                "scope_sensitive": False,
                "evidence_effects": True,
                "workflow_phase": "collection",
                "target": "lab-host",
            },
        )


def _build_runtime() -> HarnessToolRuntime:
    tool_registry = ToolRegistry()
    tool_registry.register(_DummyTool())
    return HarnessToolRuntime(
        available_skills=[],
        decision_checker=_DecisionChecker(),
        hook_registry=build_default_tool_hook_registry(),
        tool_registry=tool_registry,
    )


def _prepare_execution(runtime: HarnessToolRuntime) -> tuple[Any, ToolCallRequest]:
    request = ToolCallRequest(
        tool_call_id="dummy-call-1",
        tool_name="dummy_tool",
        arguments={"command": "  nmap   -sV   target.local  "},
    )
    prepared = prepare_tool_execution(
        runtime=runtime,
        tool_request=request,
        session=SimpleNamespace(id="session-1", runtime_policy_json={}),
        assistant_message=SimpleNamespace(id="message-1", generation_id="generation-1"),
        runtime_service=object(),
        skill_service=object(),
        mcp_service=object(),
        session_state={},
        swarm_coordinator=None,
    )
    return prepared, request


def test_default_hooks_enrich_tool_success_metadata() -> None:
    async def scenario() -> tuple[Any, Any]:
        runtime = _build_runtime()
        prepared, request = _prepare_execution(runtime)
        prepared = await apply_pre_tool_hooks(
            runtime=runtime,
            prepared=prepared,
            tool_request=request,
        )
        result = await run_tool_with_hooks(
            runtime=runtime,
            prepared=prepared,
            tool_request=request,
        )
        return prepared, result

    prepared, result = asyncio.run(scenario())

    assert prepared.pre_hooks_applied is True
    assert (
        prepared.tool_call_metadata["normalized_arguments"]["command_summary"]
        == "nmap -sV target.local"
    )
    assert prepared.started_payload["risk_level"] == "low"
    assert prepared.trace_entry["audit"]["target_summary"] is None

    assert result.event_payload["hook_status"] == "completed"
    assert result.event_payload["evidence_ids"] == ["ev-1"]
    assert result.event_payload["post_evidence_ingest"]["should_replan"] is True
    assert result.trace_entry["hook_status"] == "completed"
    assert result.trace_entry["post_evidence_ingest"]["should_replan"] is True
    assert result.step_metadata["hook_products"]["status"] == "completed"
    assert result.step_metadata["post_evidence_ingest"]["should_replan"] is True
    assert result.transcript_result_metadata["hook_products"]["status"] == "completed"
    assert result.transcript_result_metadata["post_evidence_ingest"]["should_replan"] is True


def test_default_hooks_classify_tool_execution_errors() -> None:
    async def scenario() -> Any:
        runtime = _build_runtime()
        prepared, request = _prepare_execution(runtime)
        prepared = await apply_pre_tool_hooks(
            runtime=runtime,
            prepared=prepared,
            tool_request=request,
        )
        return await notify_tool_execution_error(
            runtime=runtime,
            prepared=prepared,
            tool_request=request,
            error=ChatRuntimeError("runtime exploded"),
        )

    artifacts = asyncio.run(scenario())

    assert artifacts.trace_entry["error_classification"] == "runtime"
    assert artifacts.trace_entry["error_type"] == "ChatRuntimeError"
    assert artifacts.event_payload["error_classification"] == "runtime"
    assert artifacts.step_metadata["error_classification"] == "runtime"
    assert artifacts.transcript_tool_call_metadata["error_classification"] == "runtime"
