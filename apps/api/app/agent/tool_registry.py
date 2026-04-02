from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from uuid import uuid4

from app.agent.workflow import WorkflowExecutionContext
from app.db.models import TaskNode, TaskNodeStatus
from app.services.capabilities import CapabilityFacade


class ToolCategory(str, Enum):
    ORCHESTRATION = "orchestration"
    DISCOVERY = "discovery"
    EXECUTION = "execution"


class ToolCapability(str, Enum):
    STAGE_TRANSITION = "stage_transition"
    CAPABILITY_SNAPSHOT = "capability_snapshot"
    STRUCTURED_RUNTIME = "structured_runtime"


@dataclass(frozen=True)
class ToolSafetyProfile:
    requires_approval: bool = False
    writes_state: bool = False
    uses_runtime: bool = False
    risk_level: str = "low"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    category: ToolCategory
    capability: ToolCapability
    safety_profile: ToolSafetyProfile = field(default_factory=ToolSafetyProfile)
    description: str = ""


@dataclass(frozen=True)
class ToolExecutionRequest:
    trace_id: str
    context: WorkflowExecutionContext
    task: TaskNode
    started_at: datetime


@dataclass(frozen=True)
class ToolExecutionResult:
    spec: ToolSpec
    source_type: str
    source_name: str
    command_or_action: str
    input_payload: dict[str, object]
    output_payload: dict[str, object]
    status: TaskNodeStatus
    started_at: datetime
    ended_at: datetime


ToolMatcher = Callable[[TaskNode], bool]
ToolHandler = Callable[[ToolExecutionRequest], ToolExecutionResult]


@dataclass(frozen=True)
class _RegisteredTool:
    spec: ToolSpec
    matcher: ToolMatcher
    handler: ToolHandler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: list[_RegisteredTool] = []

    def register(self, *, spec: ToolSpec, matcher: ToolMatcher, handler: ToolHandler) -> None:
        self._tools.append(_RegisteredTool(spec=spec, matcher=matcher, handler=handler))

    def execute(self, *, context: WorkflowExecutionContext, task: TaskNode) -> ToolExecutionResult:
        started_at = datetime.now(UTC)
        request = ToolExecutionRequest(
            trace_id=f"trace-{uuid4()}",
            context=context,
            task=task,
            started_at=started_at,
        )
        for registered_tool in self._tools:
            if registered_tool.matcher(task):
                return registered_tool.handler(request)
        raise LookupError(f"No tool registered for task '{task.name}'.")


def build_default_tool_registry(
    capability_facade: CapabilityFacade | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()

    stage_spec = ToolSpec(
        name="workflow.stage_transition",
        category=ToolCategory.ORCHESTRATION,
        capability=ToolCapability.STAGE_TRANSITION,
        description="Record workflow stage transitions.",
    )
    capability_spec = ToolSpec(
        name="workflow.capability_snapshot",
        category=ToolCategory.DISCOVERY,
        capability=ToolCapability.CAPABILITY_SNAPSHOT,
        description="Capture Skill and MCP capability snapshots.",
    )
    runtime_spec = ToolSpec(
        name="workflow.structured_runtime",
        category=ToolCategory.EXECUTION,
        capability=ToolCapability.STRUCTURED_RUNTIME,
        safety_profile=ToolSafetyProfile(writes_state=True, uses_runtime=True),
        description="Produce structured workflow runtime observations.",
    )

    def _build_input_payload(request: ToolExecutionRequest) -> dict[str, object]:
        task = request.task
        return {
            "trace_id": request.trace_id,
            "session_id": request.context.session_id,
            "workflow_run_id": request.context.workflow_run_id,
            "task_id": task.id,
            "task_name": task.name,
            "stage_key": task.metadata_json.get("stage_key"),
            "role": task.metadata_json.get("role"),
            "role_prompt": task.metadata_json.get("role_prompt"),
            "sub_agent_role_prompt": task.metadata_json.get("sub_agent_role_prompt"),
            "runtime_policy": dict(request.context.runtime_policy),
        }

    def _complete(
        request: ToolExecutionRequest,
        *,
        spec: ToolSpec,
        source_type: str,
        source_name: str,
        command_or_action: str,
        output_payload: dict[str, object],
        status: TaskNodeStatus = TaskNodeStatus.COMPLETED,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            spec=spec,
            source_type=source_type,
            source_name=source_name,
            command_or_action=command_or_action,
            input_payload=_build_input_payload(request),
            output_payload=output_payload,
            status=status,
            started_at=request.started_at,
            ended_at=datetime.now(UTC),
        )

    def _handle_stage(request: ToolExecutionRequest) -> ToolExecutionResult:
        return _complete(
            request,
            spec=stage_spec,
            source_type="coordinator",
            source_name="workflow-engine",
            command_or_action=f"transition:{request.task.name}",
            output_payload={
                "stage": request.task.name,
                "status": "entered",
                "note": "stage_transition_recorded",
            },
        )

    def _handle_capability_snapshot(request: ToolExecutionRequest) -> ToolExecutionResult:
        capability_snapshot: dict[str, object] = {}
        if capability_facade is not None:
            capability_snapshot = capability_facade.build_snapshot()
        return _complete(
            request,
            spec=capability_spec,
            source_type="runtime",
            source_name="authorized-assessment",
            command_or_action=f"execute:{request.task.name}",
            output_payload={
                "stdout": (f"{request.task.name} completed under authorized assessment policy."),
                "stderr": "",
                "exit_code": 0,
                "capability_snapshot": capability_snapshot,
                "artifacts": [
                    {
                        "type": "log",
                        "name": f"{request.task.name}.json",
                        "trace_id": request.trace_id,
                    }
                ],
                "observations": [
                    {
                        "task": request.task.name,
                        "goal": request.context.goal,
                        "stage": request.task.metadata_json.get("stage_key"),
                        "observation": (f"Structured execution completed for {request.task.name}."),
                    }
                ],
            },
        )

    def _handle_structured_runtime(request: ToolExecutionRequest) -> ToolExecutionResult:
        return _complete(
            request,
            spec=runtime_spec,
            source_type="runtime",
            source_name="authorized-assessment",
            command_or_action=f"execute:{request.task.name}",
            output_payload={
                "stdout": f"{request.task.name} completed under authorized assessment policy.",
                "stderr": "",
                "exit_code": 0,
                "capability_snapshot": {},
                "artifacts": [
                    {
                        "type": "log",
                        "name": f"{request.task.name}.json",
                        "trace_id": request.trace_id,
                    }
                ],
                "observations": [
                    {
                        "task": request.task.name,
                        "goal": request.context.goal,
                        "stage": request.task.metadata_json.get("stage_key"),
                        "observation": (f"Structured execution completed for {request.task.name}."),
                    }
                ],
            },
        )

    registry.register(
        spec=stage_spec,
        matcher=lambda task: str(task.metadata_json.get("kind") or "task") == "stage",
        handler=_handle_stage,
    )
    registry.register(
        spec=capability_spec,
        matcher=lambda task: task.name == "skill_mcp_sync.capability_snapshot",
        handler=_handle_capability_snapshot,
    )
    registry.register(
        spec=runtime_spec,
        matcher=lambda _task: True,
        handler=_handle_structured_runtime,
    )
    return registry
