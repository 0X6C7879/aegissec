from __future__ import annotations

from .orchestration_models import SkillStageTransition, SkillVerificationResult


def build_stage_transition(
    *,
    active_stage: str | None,
    replan_triggers: list[str],
    verification_result: SkillVerificationResult,
) -> SkillStageTransition:
    to_stage = verification_result.requested_next_stage
    replan_required = (
        isinstance(to_stage, str) and bool(to_stage.strip()) and to_stage != active_stage
    )
    reasons = list(verification_result.reasons)
    if replan_required and not reasons:
        reasons.append(f"Advance from '{active_stage}' to '{to_stage}'.")
    return SkillStageTransition(
        from_stage=active_stage,
        to_stage=to_stage,
        replan_required=replan_required,
        triggered_by=list(replan_triggers),
        reasons=reasons,
    )
