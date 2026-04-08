from __future__ import annotations

from dataclasses import dataclass, field

from .orchestration_models import (
    SkillOrchestrationFailurePolicy,
    SkillOrchestrationMode,
)


@dataclass(slots=True)
class SkillStagePolicy:
    stage_name: str
    mode: SkillOrchestrationMode
    failure_policy: SkillOrchestrationFailurePolicy
    max_parallel_workers: int
    allow_supporting_worker_promotion: bool = False
    include_reducer: bool = False
    include_verifier: bool = False
    replan_triggers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, object]:
        return {
            "stage_name": self.stage_name,
            "mode": self.mode.value,
            "failure_policy": self.failure_policy.value,
            "max_parallel_workers": self.max_parallel_workers,
            "allow_supporting_worker_promotion": self.allow_supporting_worker_promotion,
            "include_reducer": self.include_reducer,
            "include_verifier": self.include_verifier,
            "replan_triggers": list(self.replan_triggers),
            "notes": list(self.notes),
        }


def build_skill_stage_policy(
    *,
    workflow_stage: str | None,
    supporting_count: int,
    reference_count: int,
) -> SkillStagePolicy:
    normalized_stage = (workflow_stage or "analysis").strip().casefold() or "analysis"
    base_replan_triggers = [
        "workflow_stage_changed",
        "touched_paths_changed",
        "selected_skill_set_changed",
    ]
    max_parallel_workers = max(1, min(supporting_count, 3))

    if any(keyword in normalized_stage for keyword in ("deep", "analysis", "research", "audit")):
        return SkillStagePolicy(
            stage_name=normalized_stage,
            mode=(
                SkillOrchestrationMode.PRIMARY_WITH_PARALLEL_SUPPORTING
                if supporting_count
                else SkillOrchestrationMode.SINGLE_PRIMARY
            ),
            failure_policy=SkillOrchestrationFailurePolicy.BEST_EFFORT,
            max_parallel_workers=max_parallel_workers,
            allow_supporting_worker_promotion=supporting_count > 0,
            include_reducer=supporting_count > 0,
            include_verifier=False,
            replan_triggers=[*base_replan_triggers, "reducer_requests_replan"],
            notes=[
                "Analysis stages can fan supporting skills out as candidate workers once "
                "an executor exists.",
                "Public runtime usage remains primary-only until the execution phase lands.",
            ],
        )
    if any(
        keyword in normalized_stage
        for keyword in ("execution", "validate", "validation", "execute")
    ):
        return SkillStagePolicy(
            stage_name=normalized_stage,
            mode=(
                SkillOrchestrationMode.PRIMARY_WITH_PARALLEL_SUPPORTING
                if supporting_count
                else SkillOrchestrationMode.SINGLE_PRIMARY
            ),
            failure_policy=SkillOrchestrationFailurePolicy.FAIL_FAST,
            max_parallel_workers=max_parallel_workers,
            allow_supporting_worker_promotion=supporting_count > 0,
            include_reducer=supporting_count > 1,
            include_verifier=True,
            replan_triggers=[*base_replan_triggers, "verifier_requests_replan"],
            notes=[
                "Execution-stage orchestration prefers fail-fast verification before any "
                "worker promotion.",
            ],
        )
    if "verify" in normalized_stage:
        return SkillStagePolicy(
            stage_name=normalized_stage,
            mode=SkillOrchestrationMode.SINGLE_PRIMARY,
            failure_policy=SkillOrchestrationFailurePolicy.FAIL_FAST,
            max_parallel_workers=1,
            allow_supporting_worker_promotion=False,
            include_reducer=supporting_count > 0 or reference_count > 0,
            include_verifier=True,
            replan_triggers=[*base_replan_triggers, "verification_gap_detected"],
            notes=[
                "Verify stages confirm the execution outcome before any summary or replan ",
                "step and therefore do not promote supporting workers.",
            ],
        )
    if any(
        keyword in normalized_stage for keyword in ("reflect", "summary", "summarize", "replan")
    ):
        return SkillStagePolicy(
            stage_name=normalized_stage,
            mode=SkillOrchestrationMode.SINGLE_PRIMARY,
            failure_policy=SkillOrchestrationFailurePolicy.BEST_EFFORT,
            max_parallel_workers=1,
            allow_supporting_worker_promotion=False,
            include_reducer=supporting_count > 0 or reference_count > 0,
            include_verifier=True,
            replan_triggers=[*base_replan_triggers, "summary_gap_detected"],
            notes=[
                "Reflection and summary stages consolidate evidence instead of broadening "
                "execution.",
            ],
        )
    if any(
        keyword in normalized_stage
        for keyword in ("triage", "planning", "plan", "bootstrap", "scope")
    ):
        return SkillStagePolicy(
            stage_name=normalized_stage,
            mode=(
                SkillOrchestrationMode.PRIMARY_WITH_REFERENCE_SUPPORT
                if reference_count
                else SkillOrchestrationMode.SINGLE_PRIMARY
            ),
            failure_policy=SkillOrchestrationFailurePolicy.BEST_EFFORT,
            max_parallel_workers=1,
            allow_supporting_worker_promotion=False,
            include_reducer=False,
            include_verifier=False,
            replan_triggers=list(base_replan_triggers),
            notes=[
                "Planning stages keep supporting skills in context-only mode while the "
                "resolver remains authoritative.",
            ],
        )
    return SkillStagePolicy(
        stage_name=normalized_stage,
        mode=(
            SkillOrchestrationMode.PRIMARY_WITH_PARALLEL_SUPPORTING
            if supporting_count
            else SkillOrchestrationMode.SINGLE_PRIMARY
        ),
        failure_policy=SkillOrchestrationFailurePolicy.BEST_EFFORT,
        max_parallel_workers=max_parallel_workers,
        allow_supporting_worker_promotion=supporting_count > 0,
        include_reducer=supporting_count > 0,
        include_verifier=False,
        replan_triggers=list(base_replan_triggers),
        notes=[
            "Default policy keeps the selected primary authoritative and treats supporting "
            "skills as candidate workers only in the orchestration preview.",
        ],
    )
