from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol
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


class ToolAccessMode(str, Enum):
    READ = "read"
    WRITE = "write"


class ToolSideEffectLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    HIGH = "high"


@dataclass(frozen=True)
class ToolSafetyProfile:
    requires_approval: bool = False
    writes_state: bool = False
    uses_runtime: bool = False
    uses_network: bool = False
    risk_level: str = "low"


@dataclass(frozen=True)
class ToolSpec:
    name: str
    category: ToolCategory
    capability: ToolCapability
    safety_profile: ToolSafetyProfile = field(default_factory=ToolSafetyProfile)
    description: str = ""
    access_mode: ToolAccessMode | None = None
    side_effect_level: ToolSideEffectLevel = ToolSideEffectLevel.NONE
    resource_keys: tuple[str, ...] = ()


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
class ToolPolicyDecision:
    allowed: bool
    reason: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    @classmethod
    def allow(cls) -> ToolPolicyDecision:
        return cls(allowed=True)

    @classmethod
    def deny(cls, reason: str, *, metadata: dict[str, object] | None = None) -> ToolPolicyDecision:
        return cls(allowed=False, reason=reason, metadata=dict(metadata or {}))


class ToolExecutionPolicy(Protocol):
    def evaluate(self, *, request: ToolExecutionRequest, spec: ToolSpec) -> ToolPolicyDecision: ...


class ToolExecutionHooks(Protocol):
    def before_execution(self, *, request: ToolExecutionRequest, spec: ToolSpec) -> None: ...

    def after_execution(
        self, *, request: ToolExecutionRequest, result: ToolExecutionResult
    ) -> None: ...

    def on_execution_error(
        self, *, request: ToolExecutionRequest, spec: ToolSpec, error: Exception
    ) -> None: ...


class DefaultToolExecutionPolicy:
    def evaluate(self, *, request: ToolExecutionRequest, spec: ToolSpec) -> ToolPolicyDecision:
        runtime_policy = request.context.runtime_policy
        allow_write = runtime_policy.get("allow_write")
        if spec.safety_profile.writes_state and allow_write is False:
            return ToolPolicyDecision.deny(
                "write access denied by runtime policy",
                metadata={
                    "allow_write": False,
                    "writes_state": True,
                },
            )
        allow_network = runtime_policy.get("allow_network")
        if spec.safety_profile.uses_network and allow_network is False:
            return ToolPolicyDecision.deny(
                "network access denied by runtime policy",
                metadata={
                    "allow_network": False,
                    "uses_network": True,
                },
            )
        return ToolPolicyDecision.allow()


class NoOpToolExecutionHooks:
    def before_execution(self, *, request: ToolExecutionRequest, spec: ToolSpec) -> None:
        del request, spec

    def after_execution(
        self, *, request: ToolExecutionRequest, result: ToolExecutionResult
    ) -> None:
        del request, result

    def on_execution_error(
        self, *, request: ToolExecutionRequest, spec: ToolSpec, error: Exception
    ) -> None:
        del request, spec, error


@dataclass(frozen=True)
class _RegisteredTool:
    spec: ToolSpec
    matcher: ToolMatcher
    handler: ToolHandler


class ToolRegistry:
    def __init__(
        self,
        *,
        policy: ToolExecutionPolicy | None = None,
        hooks: ToolExecutionHooks | None = None,
    ) -> None:
        self._tools: list[_RegisteredTool] = []
        self._policy = policy or DefaultToolExecutionPolicy()
        self._hooks = hooks or NoOpToolExecutionHooks()

    def register(self, *, spec: ToolSpec, matcher: ToolMatcher, handler: ToolHandler) -> None:
        self._tools.append(_RegisteredTool(spec=spec, matcher=matcher, handler=handler))

    def resolve(self, *, task: TaskNode) -> ToolSpec:
        return self._resolve_registered_tool(task).spec

    def execute(self, *, context: WorkflowExecutionContext, task: TaskNode) -> ToolExecutionResult:
        started_at = datetime.now(UTC)
        request = ToolExecutionRequest(
            trace_id=f"trace-{uuid4()}",
            context=context,
            task=task,
            started_at=started_at,
        )
        registered_tool = self._resolve_registered_tool(task)
        policy_decision = self._policy.evaluate(request=request, spec=registered_tool.spec)
        if not policy_decision.allowed:
            return self._build_policy_denied_result(
                request=request,
                spec=registered_tool.spec,
                decision=policy_decision,
            )
        self._hooks.before_execution(request=request, spec=registered_tool.spec)
        try:
            result = registered_tool.handler(request)
        except Exception as error:
            self._hooks.on_execution_error(request=request, spec=registered_tool.spec, error=error)
            raise
        self._hooks.after_execution(request=request, result=result)
        return result

    def _resolve_registered_tool(self, task: TaskNode) -> _RegisteredTool:
        for registered_tool in self._tools:
            if registered_tool.matcher(task):
                return registered_tool
        raise LookupError(f"No tool registered for task '{task.name}'.")

    @staticmethod
    def _default_source_type(spec: ToolSpec) -> str:
        return "coordinator" if spec.category is ToolCategory.ORCHESTRATION else "runtime"

    @staticmethod
    def _default_source_name(spec: ToolSpec) -> str:
        return (
            "workflow-engine"
            if spec.category is ToolCategory.ORCHESTRATION
            else "authorized-assessment"
        )

    @staticmethod
    def _default_command_or_action(task: TaskNode, spec: ToolSpec) -> str:
        if spec.capability is ToolCapability.STAGE_TRANSITION:
            return f"transition:{task.name}"
        return f"execute:{task.name}"

    def _build_policy_denied_result(
        self,
        *,
        request: ToolExecutionRequest,
        spec: ToolSpec,
        decision: ToolPolicyDecision,
    ) -> ToolExecutionResult:
        output_payload = {
            "stdout": "",
            "stderr": decision.reason or "tool execution denied by policy",
            "exit_code": 1,
            "policy_denied": True,
            "policy_allowed": False,
            "policy_reason": decision.reason,
            "policy_metadata": dict(decision.metadata),
        }
        return ToolExecutionResult(
            spec=spec,
            source_type=self._default_source_type(spec),
            source_name=self._default_source_name(spec),
            command_or_action=self._default_command_or_action(request.task, spec),
            input_payload={
                "trace_id": request.trace_id,
                "session_id": request.context.session_id,
                "workflow_run_id": request.context.workflow_run_id,
                "project_id": request.context.project_id,
                "task_id": request.task.id,
                "task_name": request.task.name,
                "stage_key": request.task.metadata_json.get("stage_key"),
                "role": request.task.metadata_json.get("role"),
                "role_prompt": request.task.metadata_json.get("role_prompt"),
                "sub_agent_role_prompt": request.task.metadata_json.get("sub_agent_role_prompt"),
                "runtime_policy": dict(request.context.runtime_policy),
                "retrieval": dict(request.context.retrieval),
                "memory": dict(request.context.memory),
                "context_projection": dict(request.context.context_projection),
                "prompting": dict(request.context.prompting),
            },
            output_payload=output_payload,
            status=TaskNodeStatus.FAILED,
            started_at=request.started_at,
            ended_at=datetime.now(UTC),
        )


def build_default_tool_registry(
    capability_facade: CapabilityFacade | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()

    stage_spec = ToolSpec(
        name="workflow.stage_transition",
        category=ToolCategory.ORCHESTRATION,
        capability=ToolCapability.STAGE_TRANSITION,
        access_mode=ToolAccessMode.WRITE,
        side_effect_level=ToolSideEffectLevel.LOW,
        resource_keys=("workflow.stage",),
        description="Record workflow stage transitions.",
    )
    capability_spec = ToolSpec(
        name="workflow.capability_snapshot",
        category=ToolCategory.DISCOVERY,
        capability=ToolCapability.CAPABILITY_SNAPSHOT,
        access_mode=ToolAccessMode.WRITE,
        side_effect_level=ToolSideEffectLevel.LOW,
        resource_keys=("workflow.capability_snapshot",),
        description="Capture Skill and MCP capability snapshots.",
    )
    runtime_spec = ToolSpec(
        name="workflow.structured_runtime",
        category=ToolCategory.EXECUTION,
        capability=ToolCapability.STRUCTURED_RUNTIME,
        safety_profile=ToolSafetyProfile(writes_state=True, uses_runtime=True),
        access_mode=ToolAccessMode.WRITE,
        side_effect_level=ToolSideEffectLevel.HIGH,
        resource_keys=("workflow.runtime",),
        description="Produce structured workflow runtime observations.",
    )

    def _build_input_payload(request: ToolExecutionRequest) -> dict[str, object]:
        task = request.task
        return {
            "trace_id": request.trace_id,
            "session_id": request.context.session_id,
            "workflow_run_id": request.context.workflow_run_id,
            "project_id": request.context.project_id,
            "task_id": task.id,
            "task_name": task.name,
            "stage_key": task.metadata_json.get("stage_key"),
            "role": task.metadata_json.get("role"),
            "role_prompt": task.metadata_json.get("role_prompt"),
            "sub_agent_role_prompt": task.metadata_json.get("sub_agent_role_prompt"),
            "runtime_policy": dict(request.context.runtime_policy),
            "retrieval": dict(request.context.retrieval),
            "memory": dict(request.context.memory),
            "context_projection": dict(request.context.context_projection),
            "prompting": dict(request.context.prompting),
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
            capability_snapshot = capability_facade.build_snapshot(
                session_id=request.context.session_id
            )
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
