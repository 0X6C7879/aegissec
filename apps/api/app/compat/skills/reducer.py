from __future__ import annotations

from .orchestration_models import (
    SkillReductionResult,
    SkillReductionStatus,
    SkillWorkerExecutionResult,
    SkillWorkerExecutionStatus,
)


def reduce_stage_results(
    *,
    stage_name: str,
    worker_results: list[SkillWorkerExecutionResult],
) -> SkillReductionResult:
    relevant_results = [
        result
        for result in worker_results
        if result.status is not SkillWorkerExecutionStatus.SKIPPED
        and result.summary_for_prompt is not None
    ]
    warnings = [warning for result in worker_results for warning in result.warnings]
    if not relevant_results:
        return SkillReductionResult(
            stage_name=stage_name,
            status=SkillReductionStatus.SKIPPED,
            summary_for_prompt="No executable worker results were available for reduction.",
            warnings=warnings,
        )

    worker_summaries = [
        result.summary_for_prompt
        for result in relevant_results
        if isinstance(result.summary_for_prompt, str) and result.summary_for_prompt.strip()
    ]
    approval_pending = [result.name for result in worker_results if result.approval_needed]
    summary_parts = [f"stage={stage_name}", f"results={len(relevant_results)}"]
    if approval_pending:
        summary_parts.append("approval_pending=" + ", ".join(approval_pending))
    summary_parts.extend(worker_summaries)
    return SkillReductionResult(
        stage_name=stage_name,
        status=SkillReductionStatus.COMPLETED,
        summary_for_prompt="; ".join(summary_parts),
        worker_summaries=worker_summaries,
        warnings=warnings,
    )
