from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Literal

from .messages import ToolCallRequest
from .tools.base import MutatingTargetClass, ToolRiskLevel

ToolExecutionLane = Literal[
    "readonly_parallel",
    "terminal_detached_parallel",
    "serial_mutating",
    "serial_high_risk",
]


@dataclass(slots=True)
class ScheduledToolCall:
    order: int
    tool_request: ToolCallRequest
    prepared: Any
    lane: ToolExecutionLane
    resource_key: str | None = None


@dataclass(slots=True)
class ScheduledToolPhase:
    lane: ToolExecutionLane
    items: list[ScheduledToolCall] = field(default_factory=list)


def _detached_terminal_resource_key(tool_request: ToolCallRequest, prepared: Any) -> str | None:
    tool = prepared.tool
    decision = prepared.decision
    if tool is None or decision is None or not decision.allowed:
        return None
    if getattr(tool, "name", None) != "execute_terminal_command":
        return None
    terminal_id = tool_request.arguments.get("terminal_id")
    if not isinstance(terminal_id, str) or not terminal_id.strip():
        return None
    if tool_request.arguments.get("detach") is not True:
        return None
    return f"terminal:{terminal_id.strip()}"


def classify_tool_execution(tool_request: ToolCallRequest, prepared: Any) -> ToolExecutionLane:
    tool = prepared.tool
    decision = prepared.decision
    if tool is None or decision is None or not decision.allowed:
        return "serial_high_risk"
    if _detached_terminal_resource_key(tool_request, prepared) is not None:
        return "terminal_detached_parallel"
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
    pending_terminal_parallel: list[ScheduledToolCall] = []

    def flush_pending() -> None:
        nonlocal pending_parallel, pending_terminal_parallel
        if pending_parallel:
            phases.append(
                ScheduledToolPhase(lane="readonly_parallel", items=list(pending_parallel))
            )
            pending_parallel = []
        if pending_terminal_parallel:
            phases.append(
                ScheduledToolPhase(
                    lane="terminal_detached_parallel",
                    items=list(pending_terminal_parallel),
                )
            )
            pending_terminal_parallel = []

    for order, (tool_request, prepared) in enumerate(
        zip(tool_requests, prepared_executions, strict=False)
    ):
        resource_key = _detached_terminal_resource_key(tool_request, prepared)
        scheduled = ScheduledToolCall(
            order=order,
            tool_request=tool_request,
            prepared=prepared,
            lane=classify_tool_execution(tool_request, prepared),
            resource_key=resource_key,
        )
        if scheduled.lane == "readonly_parallel":
            if pending_terminal_parallel:
                flush_pending()
            pending_parallel.append(scheduled)
            continue
        if scheduled.lane == "terminal_detached_parallel":
            if pending_parallel:
                flush_pending()
            pending_terminal_parallel.append(scheduled)
            continue
        flush_pending()
        phases.append(ScheduledToolPhase(lane=scheduled.lane, items=[scheduled]))
    flush_pending()
    return phases


def build_parallel_groups(phase: ScheduledToolPhase) -> list[list[ScheduledToolCall]]:
    if phase.lane != "terminal_detached_parallel":
        return [[item] for item in phase.items]

    grouped: OrderedDict[str, list[ScheduledToolCall]] = OrderedDict()
    for item in phase.items:
        resource_key = item.resource_key or f"order:{item.order}"
        grouped.setdefault(resource_key, []).append(item)
    return list(grouped.values())
