from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol
from uuid import uuid4

from app.agent.tool_runtime_models import (
    ToolExecutionEnvelope,
    ToolExecutionError,
    ToolInterruptBehavior,
    ToolRuntimeResult,
)
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
    ASK_USER_QUESTION = "ask_user_question"
    REQUEST_APPROVAL = "request_approval"


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
    is_concurrency_safe: bool | None = None
    is_read_only: bool | None = None
    is_destructive: bool | None = None
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
    input_schema: dict[str, object] = field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, object] = field(default_factory=lambda: {"type": "object"})
    input_validator: Callable[[dict[str, object], ToolExecutionRequest], None] | None = None
    input_normalizer: Callable[[dict[str, object]], dict[str, object]] | None = None
    interaction_required: bool | None = None
    interrupt_behavior_value: ToolInterruptBehavior | None = None
    use_message_renderer: Callable[[ToolExecutionRequest, dict[str, object]], str] | None = None
    result_message_renderer: (
        Callable[[ToolExecutionRequest, dict[str, object], dict[str, object]], str] | None
    ) = None
    error_message_renderer: (
        Callable[[ToolExecutionRequest, dict[str, object], dict[str, object]], str] | None
    ) = None

    def validate_input(
        self, input_payload: dict[str, object], *, request: ToolExecutionRequest
    ) -> None:
        if self.input_validator is not None:
            self.input_validator(input_payload, request)

    def normalize_input(self, input_payload: dict[str, object]) -> dict[str, object]:
        if self.input_normalizer is not None:
            return self.input_normalizer(input_payload)
        return dict(input_payload)

    def requires_user_interaction(self) -> bool:
        if self.interaction_required is not None:
            return self.interaction_required
        return self.safety_profile.requires_approval

    def interrupt_behavior(self) -> ToolInterruptBehavior:
        if self.interrupt_behavior_value is not None:
            return self.interrupt_behavior_value
        if self.requires_user_interaction():
            return ToolInterruptBehavior.REQUIRE_APPROVAL
        return ToolInterruptBehavior.NONE

    def render_tool_use_message(
        self, *, request: ToolExecutionRequest, input_payload: dict[str, object]
    ) -> str:
        if self.use_message_renderer is not None:
            return self.use_message_renderer(request, input_payload)
        return f"{self.name} started for {request.task.name}."

    def render_tool_result_message(
        self,
        *,
        request: ToolExecutionRequest,
        input_payload: dict[str, object],
        output_payload: dict[str, object],
    ) -> str:
        if self.result_message_renderer is not None:
            return self.result_message_renderer(request, input_payload, output_payload)
        summary = output_payload.get("stdout") or output_payload.get("status") or "completed"
        return f"{self.name} completed for {request.task.name}: {summary}"

    def render_tool_error_message(
        self,
        *,
        request: ToolExecutionRequest,
        input_payload: dict[str, object],
        output_payload: dict[str, object],
    ) -> str:
        if self.error_message_renderer is not None:
            return self.error_message_renderer(request, input_payload, output_payload)
        message = (
            output_payload.get("policy_reason")
            or output_payload.get("stderr")
            or output_payload.get("validation_stage")
            or "execution failed"
        )
        return f"{self.name} failed for {request.task.name}: {message}"


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
ToolHandler = Callable[[ToolExecutionRequest], ToolExecutionResult | ToolRuntimeResult]


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
        try:
            envelope = self.execute_envelope(context=context, task=task)
        except ToolExecutionError as error:
            cause = error.__cause__
            if isinstance(cause, Exception):
                raise cause
            raise
        runtime_result = envelope.runtime_result
        return ToolExecutionResult(
            spec=runtime_result.spec,
            source_type=runtime_result.source_type,
            source_name=runtime_result.source_name,
            command_or_action=runtime_result.command_or_action,
            input_payload=dict(runtime_result.input_payload),
            output_payload=dict(runtime_result.output_payload),
            status=runtime_result.status,
            started_at=runtime_result.started_at,
            ended_at=runtime_result.ended_at,
        )

    def execute_envelope(
        self, *, context: WorkflowExecutionContext, task: TaskNode
    ) -> ToolExecutionEnvelope:
        started_at = datetime.now(UTC)
        request = ToolExecutionRequest(
            trace_id=f"trace-{uuid4()}",
            context=context,
            task=task,
            started_at=started_at,
        )
        registered_tool = self._resolve_registered_tool(task)
        from app.agent.tool_pipeline import ToolPipeline, ToolPipelineRegisteredTool

        pipeline = ToolPipeline(policy=self._policy, hooks=self._hooks)
        return pipeline.execute(
            request=request,
            registered_tool=ToolPipelineRegisteredTool(
                spec=registered_tool.spec,
                handler=registered_tool.handler,
            ),
        )

    def _resolve_registered_tool(self, task: TaskNode) -> _RegisteredTool:
        for registered_tool in self._tools:
            if registered_tool.matcher(task):
                return registered_tool
        raise LookupError(f"No tool registered for task '{task.name}'.")


def build_default_tool_registry(
    capability_facade: CapabilityFacade | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()

    stage_spec = ToolSpec(
        name="workflow.stage_transition",
        category=ToolCategory.ORCHESTRATION,
        capability=ToolCapability.STAGE_TRANSITION,
        safety_profile=ToolSafetyProfile(
            writes_state=True,
            is_concurrency_safe=False,
            is_read_only=False,
            is_destructive=False,
        ),
        access_mode=ToolAccessMode.WRITE,
        side_effect_level=ToolSideEffectLevel.LOW,
        resource_keys=("workflow.stage",),
        description="Record workflow stage transitions.",
        output_schema={
            "type": "object",
            "required": ["stage", "status", "note"],
            "properties": {
                "stage": {"type": "string"},
                "status": {"type": "string"},
                "note": {"type": "string"},
            },
        },
    )
    capability_spec = ToolSpec(
        name="workflow.capability_snapshot",
        category=ToolCategory.DISCOVERY,
        capability=ToolCapability.CAPABILITY_SNAPSHOT,
        safety_profile=ToolSafetyProfile(
            writes_state=False,
            is_concurrency_safe=True,
            is_read_only=True,
            is_destructive=False,
        ),
        access_mode=ToolAccessMode.READ,
        side_effect_level=ToolSideEffectLevel.LOW,
        resource_keys=("workflow.capability_snapshot",),
        description="Capture Skill and MCP capability snapshots.",
        output_schema={
            "type": "object",
            "required": ["stdout", "stderr", "exit_code", "capability_snapshot"],
            "properties": {
                "stdout": {"type": "string"},
                "stderr": {"type": "string"},
                "exit_code": {"type": "integer"},
                "capability_snapshot": {"type": "object"},
                "artifacts": {"type": "array"},
                "observations": {"type": "array"},
            },
        },
    )
    runtime_spec = ToolSpec(
        name="workflow.structured_runtime",
        category=ToolCategory.EXECUTION,
        capability=ToolCapability.STRUCTURED_RUNTIME,
        safety_profile=ToolSafetyProfile(
            writes_state=True,
            is_concurrency_safe=False,
            is_read_only=False,
            is_destructive=True,
            uses_runtime=True,
        ),
        access_mode=ToolAccessMode.WRITE,
        side_effect_level=ToolSideEffectLevel.HIGH,
        resource_keys=("workflow.runtime",),
        description="Produce structured workflow runtime observations.",
        output_schema={
            "type": "object",
            "required": ["stdout", "stderr", "exit_code", "artifacts", "observations"],
            "properties": {
                "stdout": {"type": "string"},
                "stderr": {"type": "string"},
                "exit_code": {"type": "integer"},
                "capability_snapshot": {"type": "object"},
                "artifacts": {"type": "array"},
                "observations": {"type": "array"},
            },
        },
    )
    ask_user_question_spec = ToolSpec(
        name="workflow.ask_user_question",
        category=ToolCategory.ORCHESTRATION,
        capability=ToolCapability.ASK_USER_QUESTION,
        safety_profile=ToolSafetyProfile(
            writes_state=False,
            is_concurrency_safe=False,
            is_read_only=False,
            is_destructive=False,
        ),
        access_mode=ToolAccessMode.WRITE,
        side_effect_level=ToolSideEffectLevel.LOW,
        resource_keys=("workflow.interaction",),
        description="Pause the workflow and request user input using a protocolized payload.",
        input_schema={
            "type": "object",
            "required": ["question", "expected_fields", "context_note", "resume_hint"],
            "properties": {
                "question": {"type": "string"},
                "expected_fields": {"type": "array", "items": {"type": "string"}},
                "context_note": {"type": "string"},
                "resume_hint": {"type": "string"},
            },
        },
        interaction_required=True,
        interrupt_behavior_value=ToolInterruptBehavior.USER_INTERACTION,
        output_schema={
            "type": "object",
            "properties": {
                "stdout": {"type": "string"},
                "stderr": {"type": "string"},
                "exit_code": {"type": "integer"},
                "execution_blocked": {"type": "boolean"},
                "interaction_required": {"type": "boolean"},
                "interrupt_behavior": {"type": "string"},
                "block_reason": {"type": "string"},
                "protocol_payload": {
                    "type": "object",
                    "required": [
                        "protocol_kind",
                        "protocol_version",
                        "interaction",
                        "deferred_continuation",
                    ],
                    "properties": {
                        "protocol_kind": {"type": "string"},
                        "protocol_version": {"type": "string"},
                        "interaction": {
                            "type": "object",
                            "required": [
                                "interaction_id",
                                "question",
                                "expected_fields",
                                "context_note",
                                "resume_hint",
                            ],
                            "properties": {
                                "interaction_id": {"type": "string"},
                                "question": {"type": "string"},
                                "expected_fields": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "context_note": {"type": "string"},
                                "resume_hint": {"type": "string"},
                            },
                        },
                        "deferred_continuation": {
                            "type": "object",
                            "required": ["continuation_token", "resume_payload"],
                            "properties": {
                                "continuation_token": {"type": "string"},
                                "resume_payload": {"type": "object"},
                            },
                        },
                    },
                },
                "resume_payload": {"type": "object"},
                "continuation_token": {"type": "string"},
                "pause_reason": {"type": "string"},
                "resume_condition": {"type": "string"},
                "transcript_blocks": {"type": "array", "items": {"type": "object"}},
                "interaction_resolved": {"type": "boolean"},
                "resolution": {"type": "object"},
                "artifacts": {"type": "array"},
                "observations": {"type": "array"},
            },
        },
        input_normalizer=lambda payload: {
            **dict(payload),
            "question": str(
                payload.get("question")
                or payload.get("task_description")
                or payload.get("task_name")
                or ""
            ),
            "expected_fields": (
                [item for item in expected if isinstance(item, str)]
                if isinstance((expected := payload.get("expected_fields")), list)
                else ["user_input"]
            ),
            "context_note": str(
                payload.get("context_note")
                or payload.get("task_description")
                or payload.get("stage_key")
                or ""
            ),
            "resume_hint": str(payload.get("resume_hint") or "provide user input to resume"),
        },
        use_message_renderer=lambda request, input_payload: (
            f"{request.task.name} asks user question: {str(input_payload.get('question') or '')}"
        ),
        error_message_renderer=lambda request, input_payload, output_payload: (
            f"{request.task.name} paused for user input: "
            f"{str(output_payload.get('pause_reason') or input_payload.get('resume_hint') or '')}"
        ),
    )
    request_approval_spec = ToolSpec(
        name="workflow.request_approval",
        category=ToolCategory.ORCHESTRATION,
        capability=ToolCapability.REQUEST_APPROVAL,
        safety_profile=ToolSafetyProfile(
            requires_approval=True,
            writes_state=False,
            is_concurrency_safe=False,
            is_read_only=False,
            is_destructive=False,
        ),
        access_mode=ToolAccessMode.WRITE,
        side_effect_level=ToolSideEffectLevel.LOW,
        resource_keys=("workflow.approval",),
        description=(
            "Pause the workflow and request operator approval using a protocolized payload."
        ),
        input_schema={
            "type": "object",
            "required": ["approval_reason", "requested_scope", "risk_summary", "resume_hint"],
            "properties": {
                "approval_reason": {"type": "string"},
                "requested_scope": {"type": "string"},
                "risk_summary": {"type": "string"},
                "resume_hint": {"type": "string"},
            },
        },
        interaction_required=True,
        interrupt_behavior_value=ToolInterruptBehavior.REQUIRE_APPROVAL,
        output_schema={
            "type": "object",
            "properties": {
                "stdout": {"type": "string"},
                "stderr": {"type": "string"},
                "exit_code": {"type": "integer"},
                "execution_blocked": {"type": "boolean"},
                "interaction_required": {"type": "boolean"},
                "interrupt_behavior": {"type": "string"},
                "block_reason": {"type": "string"},
                "protocol_payload": {
                    "type": "object",
                    "required": [
                        "protocol_kind",
                        "protocol_version",
                        "approval",
                        "deferred_continuation",
                    ],
                    "properties": {
                        "protocol_kind": {"type": "string"},
                        "protocol_version": {"type": "string"},
                        "approval": {
                            "type": "object",
                            "required": [
                                "approval_id",
                                "approval_reason",
                                "requested_scope",
                                "risk_summary",
                                "resume_hint",
                            ],
                            "properties": {
                                "approval_id": {"type": "string"},
                                "approval_reason": {"type": "string"},
                                "requested_scope": {"type": "string"},
                                "risk_summary": {"type": "string"},
                                "resume_hint": {"type": "string"},
                            },
                        },
                        "deferred_continuation": {
                            "type": "object",
                            "required": ["continuation_token", "resume_payload"],
                            "properties": {
                                "continuation_token": {"type": "string"},
                                "resume_payload": {"type": "object"},
                            },
                        },
                    },
                },
                "resume_payload": {"type": "object"},
                "continuation_token": {"type": "string"},
                "pause_reason": {"type": "string"},
                "resume_condition": {"type": "string"},
                "transcript_blocks": {"type": "array", "items": {"type": "object"}},
                "approval_resolved": {"type": "boolean"},
                "resolution": {"type": "object"},
                "artifacts": {"type": "array"},
                "observations": {"type": "array"},
            },
        },
        input_normalizer=lambda payload: {
            **dict(payload),
            "approval_reason": str(
                payload.get("approval_reason")
                or payload.get("task_description")
                or payload.get("task_name")
                or ""
            ),
            "requested_scope": str(
                payload.get("requested_scope")
                or payload.get("stage_key")
                or payload.get("task_name")
                or ""
            ),
            "risk_summary": str(
                payload.get("risk_summary")
                or payload.get("task_description")
                or "Approval required for stateful workflow step."
            ),
            "resume_hint": str(payload.get("resume_hint") or "approve workflow advance to resume"),
        },
        use_message_renderer=lambda request, input_payload: (
            f"{request.task.name} requests approval: "
            f"{str(input_payload.get('approval_reason') or '')}"
        ),
        error_message_renderer=lambda request, input_payload, output_payload: (
            f"{request.task.name} paused for approval: "
            f"{str(output_payload.get('pause_reason') or input_payload.get('resume_hint') or '')}"
        ),
    )

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
            input_payload=build_tool_input_payload(request),
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

    def _handle_ask_user_question(request: ToolExecutionRequest) -> ToolExecutionResult:
        resume = dict(request.context.resume) if isinstance(request.context.resume, dict) else {}
        resolution_raw = resume.get("resolution")
        resolution = dict(resolution_raw) if isinstance(resolution_raw, dict) else {}
        user_input = str(resolution.get("user_input") or "")
        return _complete(
            request,
            spec=ask_user_question_spec,
            source_type="coordinator",
            source_name="workflow-engine",
            command_or_action=f"execute:{request.task.name}",
            output_payload={
                "stdout": user_input,
                "stderr": "",
                "exit_code": 0,
                "interaction_resolved": True,
                "resolution": resolution,
                "artifacts": [],
                "observations": [
                    {
                        "task": request.task.name,
                        "goal": request.context.goal,
                        "stage": request.task.metadata_json.get("stage_key"),
                        "observation": f"User input received for {request.task.name}.",
                    }
                ],
            },
        )

    def _handle_request_approval(request: ToolExecutionRequest) -> ToolExecutionResult:
        resume = dict(request.context.resume) if isinstance(request.context.resume, dict) else {}
        resolution_raw = resume.get("resolution")
        resolution = dict(resolution_raw) if isinstance(resolution_raw, dict) else {}
        approved = bool(resolution.get("approved", False))
        return _complete(
            request,
            spec=request_approval_spec,
            source_type="coordinator",
            source_name="workflow-engine",
            command_or_action=f"execute:{request.task.name}",
            output_payload={
                "stdout": "approved" if approved else "not_approved",
                "stderr": "",
                "exit_code": 0,
                "approval_resolved": True,
                "resolution": resolution,
                "artifacts": [],
                "observations": [
                    {
                        "task": request.task.name,
                        "goal": request.context.goal,
                        "stage": request.task.metadata_json.get("stage_key"),
                        "observation": f"Approval captured for {request.task.name}.",
                    }
                ],
            },
        )

    registry.register(
        spec=ask_user_question_spec,
        matcher=lambda task: (
            str(task.metadata_json.get("workflow_tool") or "") == "workflow.ask_user_question"
            or str(task.metadata_json.get("interrupt_behavior") or "") == "user_interaction"
            or bool(task.metadata_json.get("interaction_required", False))
        ),
        handler=_handle_ask_user_question,
    )
    registry.register(
        spec=request_approval_spec,
        matcher=lambda task: bool(task.metadata_json.get("approval_required", False)),
        handler=_handle_request_approval,
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


def build_tool_input_payload(request: ToolExecutionRequest) -> dict[str, object]:
    task = request.task
    metadata = task.metadata_json
    return {
        "trace_id": request.trace_id,
        "session_id": request.context.session_id,
        "workflow_run_id": request.context.workflow_run_id,
        "project_id": request.context.project_id,
        "task_id": task.id,
        "task_name": task.name,
        "stage_key": metadata.get("stage_key"),
        "role": metadata.get("role"),
        "role_prompt": metadata.get("role_prompt"),
        "sub_agent_role_prompt": metadata.get("sub_agent_role_prompt"),
        "task_description": metadata.get("description"),
        "question": metadata.get("question"),
        "expected_fields": metadata.get("expected_fields"),
        "context_note": metadata.get("context_note"),
        "approval_reason": metadata.get("approval_reason"),
        "requested_scope": metadata.get("requested_scope"),
        "risk_summary": metadata.get("risk_summary"),
        "resume_hint": metadata.get("resume_hint"),
        "runtime_policy": dict(request.context.runtime_policy),
        "retrieval": dict(request.context.retrieval),
        "memory": dict(request.context.memory),
        "context_projection": dict(request.context.context_projection),
        "prompting": dict(request.context.prompting),
        "resume": dict(request.context.resume),
    }
