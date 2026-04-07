from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .messages import ToolCallRequest
from .tools.base import MutatingTargetClass, ToolRiskLevel

ToolExecutionLane = Literal["readonly_parallel", "serial_mutating", "serial_high_risk"]


@dataclass(slots=True)
class ScheduledToolCall:
    order: int
    tool_request: ToolCallRequest
    prepared: Any
    lane: ToolExecutionLane


@dataclass(slots=True)
class ScheduledToolPhase:
    lane: ToolExecutionLane
    items: list[ScheduledToolCall] = field(default_factory=list)


def classify_tool_execution(prepared: Any) -> ToolExecutionLane:
    tool = prepared.tool
    decision = prepared.decision
    if tool is None or decision is None or not decision.allowed:
        return "serial_high_risk"
    if (
        tool.is_read_only()
        and tool.risk_level() is ToolRiskLevel.LOW
        and tool.mutating_target_class() is MutatingTargetClass.NONE
    ):
        return "readonly_parallel"
    if tool.risk_level() in {ToolRiskLevel.HIGH, ToolRiskLevel.DESTRUCTIVE}:
        return "serial_high_risk"
    if tool.mutating_target_class() in {
        MutatingTargetClass.RUNTIME,
        MutatingTargetClass.CONFIG,
        MutatingTargetClass.SESSION,
    }:
        return "serial_high_risk"
    return "serial_mutating"


def build_tool_schedule(
    tool_requests: list[ToolCallRequest],
    prepared_executions: list[Any],
) -> list[ScheduledToolPhase]:
    phases: list[ScheduledToolPhase] = []
    pending_parallel: list[ScheduledToolCall] = []
    for order, (tool_request, prepared) in enumerate(
        zip(tool_requests, prepared_executions, strict=False)
    ):
        scheduled = ScheduledToolCall(
            order=order,
            tool_request=tool_request,
            prepared=prepared,
            lane=classify_tool_execution(prepared),
        )
        if scheduled.lane == "readonly_parallel":
            pending_parallel.append(scheduled)
            continue
        if pending_parallel:
            phases.append(
                ScheduledToolPhase(lane="readonly_parallel", items=list(pending_parallel))
            )
            pending_parallel = []
        phases.append(ScheduledToolPhase(lane=scheduled.lane, items=[scheduled]))
    if pending_parallel:
        phases.append(ScheduledToolPhase(lane="readonly_parallel", items=list(pending_parallel)))
    return phases
