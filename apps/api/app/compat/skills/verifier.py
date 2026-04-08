from __future__ import annotations

from .orchestration_models import (
    SkillVerificationResult,
    SkillVerificationStatus,
    SkillWorkerExecutionResult,
    SkillWorkerExecutionStatus,
)


def verify_stage_results(
    *,
    stage_name: str,
    failure_policy: str | None,
    worker_results: list[SkillWorkerExecutionResult],
) -> SkillVerificationResult:
    relevant_results = [result for result in worker_results if result.required]
    warnings = [warning for result in worker_results for warning in result.warnings]
    reasons: list[str] = []
    primary_result = next(
        (result for result in worker_results if result.role.value == "primary"), None
    )
    failed_required = [
        result.name
        for result in relevant_results
        if result.status is SkillWorkerExecutionStatus.FAILED
    ]
    approval_pending = [result.name for result in relevant_results if result.approval_needed]

    if primary_result is None:
        reasons.append("Primary orchestration step is missing.")
        return SkillVerificationResult(
            stage_name=stage_name,
            status=SkillVerificationStatus.FAILED,
            passed=False,
            reasons=reasons,
            warnings=warnings,
            requested_next_stage="replan",
        )

    if primary_result.status is not SkillWorkerExecutionStatus.SUCCEEDED:
        reasons.append(f"Primary step '{primary_result.name}' did not succeed.")

    if failed_required:
        reasons.append("Required steps failed: " + ", ".join(failed_required))

    if approval_pending:
        reasons.append("Approval is still required for: " + ", ".join(approval_pending))

    passed = not reasons
    if failure_policy == "fail_fast" and approval_pending:
        warnings.append("Approval is still required for one or more execution artifacts.")

    requested_next_stage = _next_stage_name(stage_name, passed=passed)
    if approval_pending:
        requested_next_stage = stage_name
    return SkillVerificationResult(
        stage_name=stage_name,
        status=SkillVerificationStatus.PASSED if passed else SkillVerificationStatus.FAILED,
        passed=passed,
        reasons=reasons,
        warnings=warnings,
        requested_next_stage=requested_next_stage,
    )


def _next_stage_name(stage_name: str, *, passed: bool) -> str | None:
    normalized_stage = stage_name.casefold()
    if not passed:
        return "replan"
    if any(keyword in normalized_stage for keyword in ("analysis", "deep", "research", "audit")):
        return "execution"
    if any(keyword in normalized_stage for keyword in ("execution", "validate", "validation")):
        return "verify"
    if any(keyword in normalized_stage for keyword in ("verify", "reflection", "reflect")):
        return "summarize"
    return None
