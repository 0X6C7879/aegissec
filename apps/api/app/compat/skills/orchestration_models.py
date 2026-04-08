from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass(slots=True)
class SkillOrchestrationHints:
    values: dict[str, object] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        return dict(self.values)

    @property
    def is_empty(self) -> bool:
        return not self.values


@dataclass(slots=True)
class SkillExecutionPolicy:
    values: dict[str, object] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        return dict(self.values)

    @property
    def is_empty(self) -> bool:
        return not self.values


@dataclass(slots=True)
class SkillResultSchema:
    values: dict[str, object] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        return dict(self.values)

    @property
    def is_empty(self) -> bool:
        return not self.values


class SkillOrchestrationStepRole(str, Enum):
    PRIMARY = "primary"
    SUPPORTING = "supporting"
    REFERENCE = "reference"
    REDUCER = "reducer"
    VERIFIER = "verifier"


class SkillExecutionIntent(str, Enum):
    EXECUTE_PRIMARY = "execute_primary"
    CANDIDATE_WORKER = "candidate_worker"
    PREPARE_CONTEXT = "prepare_context"
    REFERENCE_ONLY = "reference_only"
    REDUCE_RESULTS = "reduce_results"
    VERIFY_RESULTS = "verify_results"


class SkillOrchestrationMode(str, Enum):
    SINGLE_PRIMARY = "single_primary"
    PRIMARY_WITH_PARALLEL_SUPPORTING = "primary_with_parallel_supporting"
    PRIMARY_WITH_REFERENCE_SUPPORT = "primary_with_reference_support"


class SkillOrchestrationFailurePolicy(str, Enum):
    FAIL_FAST = "fail_fast"
    BEST_EFFORT = "best_effort"


class SkillWorkerExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class SkillReductionStatus(str, Enum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class SkillVerificationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(slots=True)
class SkillOrchestrationConcurrencyGroup:
    group_id: str
    step_ids: list[str] = field(default_factory=list)
    rationale: str | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "group_id": self.group_id,
            "step_ids": list(self.step_ids),
        }
        if self.rationale is not None:
            payload["rationale"] = self.rationale
        return payload


@dataclass(slots=True)
class SkillOrchestrationStep:
    step_id: str
    name: str
    role: SkillOrchestrationStepRole
    execution_intent: SkillExecutionIntent
    stage_name: str
    skill_id: str | None = None
    directory_name: str | None = None
    node_kind: str = "skill"
    internal_node: bool = False
    prepared_for_context: bool = False
    prepared_for_execution: bool = False
    trust_level: str | None = None
    verification_mode: str | None = None
    version: str | None = None
    model_hint: str | None = None
    fanout_group: str | None = None
    context_strategy: str | None = None
    execution_policy: dict[str, object] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    preflight_checks: list[dict[str, object]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "step_id": self.step_id,
            "name": self.name,
            "role": self.role.value,
            "execution_intent": self.execution_intent.value,
            "stage_name": self.stage_name,
            "prepared_for_context": self.prepared_for_context,
            "prepared_for_execution": self.prepared_for_execution,
            "depends_on": list(self.depends_on),
            "preflight_checks": [dict(item) for item in self.preflight_checks],
            "notes": list(self.notes),
        }
        if self.skill_id is not None:
            payload["skill_id"] = self.skill_id
        if self.directory_name is not None:
            payload["directory_name"] = self.directory_name
        payload["node_kind"] = self.node_kind
        payload["internal_node"] = self.internal_node
        if self.trust_level is not None:
            payload["trust_level"] = self.trust_level
        if self.verification_mode is not None:
            payload["verification_mode"] = self.verification_mode
        if self.version is not None:
            payload["version"] = self.version
        if self.model_hint is not None:
            payload["model_hint"] = self.model_hint
        if self.fanout_group is not None:
            payload["fanout_group"] = self.fanout_group
        if self.context_strategy is not None:
            payload["context_strategy"] = self.context_strategy
        if self.execution_policy:
            payload["execution_policy"] = dict(self.execution_policy)
        return payload


@dataclass(slots=True)
class SkillOrchestrationStage:
    stage_name: str
    mode: SkillOrchestrationMode
    failure_policy: SkillOrchestrationFailurePolicy
    max_parallel_workers: int
    worker_timeout_ms: int | None = None
    orchestration_timeout_ms: int | None = None
    retry_limit: int = 0
    steps: list[SkillOrchestrationStep] = field(default_factory=list)
    concurrency_groups: list[SkillOrchestrationConcurrencyGroup] = field(default_factory=list)
    replan_triggers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, object]:
        return {
            "stage_name": self.stage_name,
            "mode": self.mode.value,
            "failure_policy": self.failure_policy.value,
            "max_parallel_workers": self.max_parallel_workers,
            "worker_timeout_ms": self.worker_timeout_ms,
            "orchestration_timeout_ms": self.orchestration_timeout_ms,
            "retry_limit": self.retry_limit,
            "steps": [step.to_payload() for step in self.steps],
            "concurrency_groups": [group.to_payload() for group in self.concurrency_groups],
            "replan_triggers": list(self.replan_triggers),
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class SkillOrchestrationPlan:
    workflow_stage: str | None
    agent_role: str | None
    primary_skill_id: str | None
    selected_skill_ids: list[str] = field(default_factory=list)
    reference_skill_ids: list[str] = field(default_factory=list)
    suppressed_skill_ids: list[str] = field(default_factory=list)
    active_stage: str | None = None
    stages: list[SkillOrchestrationStage] = field(default_factory=list)
    replan_triggers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, object]:
        return {
            "workflow_stage": self.workflow_stage,
            "agent_role": self.agent_role,
            "primary_skill_id": self.primary_skill_id,
            "selected_skill_ids": list(self.selected_skill_ids),
            "reference_skill_ids": list(self.reference_skill_ids),
            "suppressed_skill_ids": list(self.suppressed_skill_ids),
            "active_stage": self.active_stage,
            "stages": [stage.to_payload() for stage in self.stages],
            "replan_triggers": list(self.replan_triggers),
            "notes": list(self.notes),
        }


@dataclass(slots=True)
class SkillWorkerExecutionResult:
    step_id: str
    stage_name: str
    name: str
    role: SkillOrchestrationStepRole
    execution_intent: SkillExecutionIntent
    status: SkillWorkerExecutionStatus
    skill_id: str | None = None
    node_kind: str = "skill"
    internal_node: bool = False
    duration_ms: int = 0
    trust_level: str | None = None
    version: str | None = None
    model_hint: str | None = None
    prepared_for_context: bool = False
    prepared_for_execution: bool = False
    required: bool = True
    preflight_results: list[dict[str, object]] = field(default_factory=list)
    execution_output: dict[str, object] | None = None
    warnings: list[str] = field(default_factory=list)
    approval_needed: bool = False
    attempt_count: int = 1
    retry_count: int = 0
    timeout_ms: int | None = None
    cancelled: bool = False
    timed_out: bool = False
    cancellation_reason: str | None = None
    failure_reason: str | None = None
    summary_for_prompt: str | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "step_id": self.step_id,
            "stage_name": self.stage_name,
            "name": self.name,
            "role": self.role.value,
            "execution_intent": self.execution_intent.value,
            "status": self.status.value,
            "node_kind": self.node_kind,
            "internal_node": self.internal_node,
            "duration_ms": self.duration_ms,
            "prepared_for_context": self.prepared_for_context,
            "prepared_for_execution": self.prepared_for_execution,
            "required": self.required,
            "preflight_results": [dict(item) for item in self.preflight_results],
            "warnings": list(self.warnings),
            "approval_needed": self.approval_needed,
            "attempt_count": self.attempt_count,
            "retry_count": self.retry_count,
            "cancelled": self.cancelled,
            "timed_out": self.timed_out,
        }
        if self.skill_id is not None:
            payload["skill_id"] = self.skill_id
        if self.trust_level is not None:
            payload["trust_level"] = self.trust_level
        if self.version is not None:
            payload["version"] = self.version
        if self.model_hint is not None:
            payload["model_hint"] = self.model_hint
        if self.timeout_ms is not None:
            payload["timeout_ms"] = self.timeout_ms
        if self.execution_output is not None:
            payload["execution_output"] = dict(self.execution_output)
        if self.cancellation_reason is not None:
            payload["cancellation_reason"] = self.cancellation_reason
        if self.failure_reason is not None:
            payload["failure_reason"] = self.failure_reason
        if self.summary_for_prompt is not None:
            payload["summary_for_prompt"] = self.summary_for_prompt
        return payload


@dataclass(slots=True)
class SkillReductionResult:
    stage_name: str
    status: SkillReductionStatus
    summary_for_prompt: str | None = None
    worker_summaries: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "stage_name": self.stage_name,
            "status": self.status.value,
            "worker_summaries": list(self.worker_summaries),
            "warnings": list(self.warnings),
        }
        if self.summary_for_prompt is not None:
            payload["summary_for_prompt"] = self.summary_for_prompt
        return payload


@dataclass(slots=True)
class SkillVerificationResult:
    stage_name: str
    status: SkillVerificationStatus
    passed: bool
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requested_next_stage: str | None = None

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "stage_name": self.stage_name,
            "status": self.status.value,
            "passed": self.passed,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
        }
        if self.requested_next_stage is not None:
            payload["requested_next_stage"] = self.requested_next_stage
        return payload


@dataclass(slots=True)
class SkillStageTransition:
    from_stage: str | None
    to_stage: str | None
    replan_required: bool
    triggered_by: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, object]:
        return {
            "from_stage": self.from_stage,
            "to_stage": self.to_stage,
            "replan_required": self.replan_required,
            "triggered_by": list(self.triggered_by),
            "reasons": list(self.reasons),
        }


@dataclass(slots=True)
class SkillOrchestrationExecutionResult:
    active_stage: str | None
    mode: str | None
    failure_policy: str | None
    status: str
    duration_ms: int
    worker_results: list[SkillWorkerExecutionResult] = field(default_factory=list)
    node_results: list[SkillWorkerExecutionResult] = field(default_factory=list)
    reduction_result: SkillReductionResult | None = None
    verification_result: SkillVerificationResult | None = None
    stage_transition: SkillStageTransition | None = None
    orchestration_timeout_ms: int | None = None
    cancelled: bool = False
    timed_out: bool = False
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "active_stage": self.active_stage,
            "mode": self.mode,
            "failure_policy": self.failure_policy,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "worker_results": [result.to_payload() for result in self.worker_results],
            "node_results": [result.to_payload() for result in self.node_results],
            "orchestration_timeout_ms": self.orchestration_timeout_ms,
            "cancelled": self.cancelled,
            "timed_out": self.timed_out,
            "warnings": list(self.warnings),
            "notes": list(self.notes),
        }
        if self.reduction_result is not None:
            payload["reduction_result"] = self.reduction_result.to_payload()
        if self.verification_result is not None:
            payload["verification_result"] = self.verification_result.to_payload()
        if self.stage_transition is not None:
            payload["stage_transition"] = self.stage_transition.to_payload()
        return payload
