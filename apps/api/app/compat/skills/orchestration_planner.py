from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from app.compat.skills import models as skill_models

from .orchestration_models import (
    SkillExecutionIntent,
    SkillOrchestrationConcurrencyGroup,
    SkillOrchestrationPlan,
    SkillOrchestrationStage,
    SkillOrchestrationStep,
    SkillOrchestrationStepRole,
)
from .stage_policy import build_skill_stage_policy


class _SkillSetPlanProtocol(Protocol):
    primary_candidate: skill_models.ResolvedSkillCandidate | None
    supporting_candidates: list[skill_models.ResolvedSkillCandidate]
    selected_candidates: list[skill_models.ResolvedSkillCandidate]
    reference_candidates: list[skill_models.ResolvedSkillCandidate]
    suppressed_candidates: list[skill_models.ResolvedSkillCandidate]
    workflow_stage: str | None
    agent_role: str | None
    selected_skill_ids: list[str]
    notes: list[str]


@dataclass(slots=True)
class SkillOrchestrationPlannerOutput:
    plan: SkillOrchestrationPlan

    def to_payload(self) -> dict[str, object]:
        return self.plan.to_payload()


def build_skill_orchestration_plan(
    skill_set_plan: _SkillSetPlanProtocol,
) -> SkillOrchestrationPlannerOutput:
    stage_policy = build_skill_stage_policy(
        workflow_stage=skill_set_plan.workflow_stage,
        supporting_count=len(skill_set_plan.supporting_candidates),
        reference_count=len(skill_set_plan.reference_candidates),
    )
    stage_name = stage_policy.stage_name
    steps: list[SkillOrchestrationStep] = []
    executable_step_ids: list[str] = []
    candidate_worker_step_ids: list[str] = []

    primary_candidate = skill_set_plan.primary_candidate
    if primary_candidate is not None:
        primary_skill = primary_candidate.compiled_skill
        primary_step_id = _skill_step_id(primary_skill.skill_id)
        executable_step_ids.append(primary_step_id)
        steps.append(
            SkillOrchestrationStep(
                step_id=primary_step_id,
                skill_id=primary_skill.skill_id,
                name=primary_skill.name,
                directory_name=primary_skill.directory_name,
                role=SkillOrchestrationStepRole.PRIMARY,
                execution_intent=SkillExecutionIntent.EXECUTE_PRIMARY,
                stage_name=stage_name,
                prepared_for_context=True,
                prepared_for_execution=True,
                trust_level=_trust_level(primary_skill),
                verification_mode=_verification_mode(primary_skill),
                fanout_group=primary_skill.fanout_group,
                context_strategy=primary_skill.context_strategy,
                preflight_checks=_preflight_payloads(primary_skill.preflight_checks),
                notes=_candidate_notes(primary_candidate),
            )
        )

    for candidate in skill_set_plan.supporting_candidates:
        skill = candidate.compiled_skill
        step_id = _skill_step_id(skill.skill_id)
        execution_intent = (
            SkillExecutionIntent.CANDIDATE_WORKER
            if stage_policy.allow_supporting_worker_promotion
            else SkillExecutionIntent.PREPARE_CONTEXT
        )
        if execution_intent is SkillExecutionIntent.CANDIDATE_WORKER:
            candidate_worker_step_ids.append(step_id)
        steps.append(
            SkillOrchestrationStep(
                step_id=step_id,
                skill_id=skill.skill_id,
                name=skill.name,
                directory_name=skill.directory_name,
                role=SkillOrchestrationStepRole.SUPPORTING,
                execution_intent=execution_intent,
                stage_name=stage_name,
                prepared_for_context=True,
                prepared_for_execution=False,
                trust_level=_trust_level(skill),
                verification_mode=_verification_mode(skill),
                fanout_group=skill.fanout_group,
                context_strategy=skill.context_strategy,
                preflight_checks=_preflight_payloads(skill.preflight_checks),
                notes=_candidate_notes(candidate),
            )
        )

    for candidate in skill_set_plan.reference_candidates:
        skill = candidate.compiled_skill
        steps.append(
            SkillOrchestrationStep(
                step_id=_skill_step_id(skill.skill_id),
                skill_id=skill.skill_id,
                name=skill.name,
                directory_name=skill.directory_name,
                role=SkillOrchestrationStepRole.REFERENCE,
                execution_intent=SkillExecutionIntent.REFERENCE_ONLY,
                stage_name=stage_name,
                prepared_for_context=False,
                prepared_for_execution=False,
                trust_level=_trust_level(skill),
                verification_mode=_verification_mode(skill),
                fanout_group=skill.fanout_group,
                context_strategy=skill.context_strategy,
                notes=_candidate_notes(candidate),
            )
        )

    concurrency_groups: list[SkillOrchestrationConcurrencyGroup] = []
    if len(candidate_worker_step_ids) > 1:
        concurrency_groups.append(
            SkillOrchestrationConcurrencyGroup(
                group_id=f"{stage_name}:supporting-workers",
                step_ids=list(candidate_worker_step_ids),
                rationale=(
                    "Supporting skills can fan out in the preview while public runtime "
                    "execution remains primary-only."
                ),
            )
        )

    reducer_dependencies = [*executable_step_ids, *candidate_worker_step_ids]
    if stage_policy.include_reducer and reducer_dependencies:
        reducer_step_id = f"stage:{stage_name}:reducer"
        steps.append(
            SkillOrchestrationStep(
                step_id=reducer_step_id,
                name=f"{stage_name} reducer",
                role=SkillOrchestrationStepRole.REDUCER,
                execution_intent=SkillExecutionIntent.REDUCE_RESULTS,
                stage_name=stage_name,
                depends_on=list(reducer_dependencies),
                notes=[
                    "Reducer stays as an internal orchestration step until the executor "
                    "phase is implemented."
                ],
            )
        )
        reducer_dependencies = [reducer_step_id]

    if stage_policy.include_verifier:
        verifier_dependencies = (
            reducer_dependencies or executable_step_ids or candidate_worker_step_ids
        )
        steps.append(
            SkillOrchestrationStep(
                step_id=f"stage:{stage_name}:verifier",
                name=f"{stage_name} verifier",
                role=SkillOrchestrationStepRole.VERIFIER,
                execution_intent=SkillExecutionIntent.VERIFY_RESULTS,
                stage_name=stage_name,
                depends_on=list(verifier_dependencies),
                notes=[
                    "Verifier is represented as a typed plan step only; fixed facades "
                    "remain unchanged."
                ],
            )
        )

    stage = SkillOrchestrationStage(
        stage_name=stage_name,
        mode=stage_policy.mode,
        failure_policy=stage_policy.failure_policy,
        max_parallel_workers=stage_policy.max_parallel_workers,
        steps=steps,
        concurrency_groups=concurrency_groups,
        replan_triggers=list(stage_policy.replan_triggers),
        notes=[
            *stage_policy.notes,
            "Supporting skills remain `prepared_for_execution=false` in public runtime "
            "usage until a later execution phase.",
        ],
    )
    plan = SkillOrchestrationPlan(
        workflow_stage=skill_set_plan.workflow_stage,
        agent_role=skill_set_plan.agent_role,
        primary_skill_id=(
            None if primary_candidate is None else primary_candidate.compiled_skill.skill_id
        ),
        selected_skill_ids=list(skill_set_plan.selected_skill_ids),
        reference_skill_ids=[
            candidate.compiled_skill.skill_id for candidate in skill_set_plan.reference_candidates
        ],
        suppressed_skill_ids=[
            candidate.compiled_skill.skill_id for candidate in skill_set_plan.suppressed_candidates
        ],
        active_stage=stage_name,
        stages=[stage],
        replan_triggers=list(stage_policy.replan_triggers),
        notes=[
            *skill_set_plan.notes,
            "This orchestration plan is additive and does not change resolver scoring or "
            "fixed facade execution semantics.",
        ],
    )
    return SkillOrchestrationPlannerOutput(plan=plan)


def _skill_step_id(skill_id: str) -> str:
    return f"skill:{skill_id}"


def _candidate_notes(candidate: skill_models.ResolvedSkillCandidate) -> list[str]:
    notes: list[str] = []
    if candidate.reasons:
        notes.append("; ".join(candidate.reasons[:2]))
    why_selected = candidate.selection_explanation.get("why_selected")
    if isinstance(why_selected, str) and why_selected.strip():
        notes.append(why_selected)
    why_packed = candidate.packing_explanation.get("why_selected")
    if isinstance(why_packed, str) and why_packed.strip():
        notes.append(why_packed)
    return notes


def _preflight_payloads(
    checks: Iterable[object],
) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for check in checks:
        to_payload = getattr(check, "to_payload", None)
        if callable(to_payload):
            raw_payload = to_payload()
            if isinstance(raw_payload, dict):
                payloads.append(dict(raw_payload))
    return payloads


def _trust_level(compiled_skill: skill_models.CompiledSkill) -> str | None:
    metadata = compiled_skill.trust_metadata
    return None if metadata is None else metadata.trust_level


def _verification_mode(compiled_skill: skill_models.CompiledSkill) -> str | None:
    metadata = compiled_skill.trust_metadata
    return None if metadata is None else metadata.verification_mode
