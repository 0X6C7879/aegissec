from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from subprocess import run
from time import perf_counter
from typing import Any, Protocol, cast

from .orchestration_models import (
    SkillExecutionIntent,
    SkillOrchestrationExecutionResult,
    SkillOrchestrationStepRole,
    SkillReductionResult,
    SkillWorkerExecutionResult,
    SkillWorkerExecutionStatus,
)
from .preflight import (
    SkillPreflightCheck,
    SkillPreflightResult,
    SkillPreflightResultStatus,
    can_auto_run_preflight,
    normalize_preflight_kind,
)
from .reducer import reduce_stage_results
from .replanning import build_stage_transition
from .verifier import verify_stage_results


class _SkillFacadeExecutor(Protocol):
    def __call__(
        self,
        name_or_slug: str,
        *,
        arguments: dict[str, object] | None,
        workspace_path: str | None,
        touched_paths: list[str] | None,
        session_id: str | None,
    ) -> dict[str, Any]: ...


def execute_skill_orchestration_plan(
    *,
    orchestration_plan: dict[str, object],
    execute_skill_facade: _SkillFacadeExecutor,
    arguments: dict[str, object] | None,
    workspace_path: str | None,
    touched_paths: list[str] | None,
    session_id: str | None,
) -> SkillOrchestrationExecutionResult:
    started_at = perf_counter()
    active_stage = cast(str | None, orchestration_plan.get("active_stage"))
    stage_payload = _resolve_active_stage(orchestration_plan, active_stage=active_stage)
    if stage_payload is None:
        duration_ms = int((perf_counter() - started_at) * 1000)
        return SkillOrchestrationExecutionResult(
            active_stage=active_stage,
            mode=None,
            failure_policy=None,
            status="skipped",
            duration_ms=duration_ms,
            notes=["No orchestration stage payload was available for execution."],
        )

    steps = cast(list[dict[str, object]], stage_payload.get("steps", []))
    executable_steps = [step for step in steps if _is_executable_step(step)]
    max_parallel_workers = _max_worker_count(stage_payload, executable_steps)
    result_map: dict[str, SkillWorkerExecutionResult] = {}

    if executable_steps:
        with ThreadPoolExecutor(max_workers=max_parallel_workers) as executor:
            future_map = {
                executor.submit(
                    _execute_worker_step,
                    step=step,
                    execute_skill_facade=execute_skill_facade,
                    arguments=arguments,
                    workspace_path=workspace_path,
                    touched_paths=touched_paths,
                    session_id=session_id,
                ): step
                for step in executable_steps
            }
            for future in as_completed(future_map):
                result = future.result()
                result_map[result.step_id] = result

    ordered_results = [
        result_map.get(cast(str, step.get("step_id"))) or _skipped_worker_result(step)
        for step in steps
        if isinstance(step, dict)
    ]
    reduction_result = _build_reduction_result(stage_payload, ordered_results)
    verification_result = verify_stage_results(
        stage_name=str(stage_payload.get("stage_name") or active_stage or "analysis"),
        failure_policy=cast(str | None, stage_payload.get("failure_policy")),
        worker_results=ordered_results,
    )
    stage_transition = build_stage_transition(
        active_stage=active_stage,
        replan_triggers=cast(list[str], orchestration_plan.get("replan_triggers", [])),
        verification_result=verification_result,
    )
    duration_ms = int((perf_counter() - started_at) * 1000)
    return SkillOrchestrationExecutionResult(
        active_stage=active_stage,
        mode=cast(str | None, stage_payload.get("mode")),
        failure_policy=cast(str | None, stage_payload.get("failure_policy")),
        status="completed" if verification_result.passed else "failed",
        duration_ms=duration_ms,
        worker_results=ordered_results,
        reduction_result=reduction_result,
        verification_result=verification_result,
        stage_transition=stage_transition,
        warnings=[warning for result in ordered_results for warning in result.warnings],
        notes=["Execution stays above fixed skill facades and preserves public payload semantics."],
    )


def _resolve_active_stage(
    orchestration_plan: dict[str, object], *, active_stage: str | None
) -> dict[str, object] | None:
    raw_stages = orchestration_plan.get("stages")
    if not isinstance(raw_stages, list):
        return None
    for stage in raw_stages:
        if isinstance(stage, dict) and stage.get("stage_name") == active_stage:
            return stage
    return next((stage for stage in raw_stages if isinstance(stage, dict)), None)


def _is_executable_step(step: dict[str, object]) -> bool:
    intent = str(step.get("execution_intent") or "")
    return intent in {
        SkillExecutionIntent.EXECUTE_PRIMARY.value,
        SkillExecutionIntent.CANDIDATE_WORKER.value,
    }


def _max_worker_count(stage_payload: dict[str, object], steps: list[dict[str, object]]) -> int:
    configured = stage_payload.get("max_parallel_workers")
    worker_budget = configured if isinstance(configured, int) and configured > 0 else 1
    primary_slots = (
        1
        if any(step.get("role") == SkillOrchestrationStepRole.PRIMARY.value for step in steps)
        else 0
    )
    return max(1, min(len(steps), worker_budget + primary_slots))


def _execute_worker_step(
    *,
    step: dict[str, object],
    execute_skill_facade: _SkillFacadeExecutor,
    arguments: dict[str, object] | None,
    workspace_path: str | None,
    touched_paths: list[str] | None,
    session_id: str | None,
) -> SkillWorkerExecutionResult:
    started_at = perf_counter()
    name = str(step.get("name") or step.get("directory_name") or step.get("skill_id") or "unknown")
    skill_id = cast(str | None, step.get("skill_id"))
    identifier = skill_id or cast(str | None, step.get("directory_name")) or name
    trust_level = cast(str | None, step.get("trust_level"))
    preflight_results = _run_preflight_checks(
        cast(list[dict[str, object]], step.get("preflight_checks", [])),
        trust_level=trust_level,
        workspace_path=workspace_path,
    )
    warnings = [
        cast(str, payload["warning"])
        for payload in (result.to_payload() for result in preflight_results)
        if isinstance(payload.get("warning"), str)
    ]
    approval_needed = any(
        result.status is SkillPreflightResultStatus.APPROVAL_REQUIRED
        for result in preflight_results
    )
    failed_required_preflights = [
        result.name
        for result in preflight_results
        if result.required and result.status is SkillPreflightResultStatus.FAILED
    ]
    approval_required_preflights = [
        result.name
        for result in preflight_results
        if result.required and result.status is SkillPreflightResultStatus.APPROVAL_REQUIRED
    ]
    if failed_required_preflights:
        return SkillWorkerExecutionResult(
            step_id=str(step.get("step_id") or identifier),
            stage_name=str(step.get("stage_name") or "analysis"),
            name=name,
            role=SkillOrchestrationStepRole(str(step.get("role") or "supporting")),
            execution_intent=SkillExecutionIntent(
                str(step.get("execution_intent") or "prepare_context")
            ),
            status=SkillWorkerExecutionStatus.FAILED,
            skill_id=skill_id,
            duration_ms=int((perf_counter() - started_at) * 1000),
            trust_level=trust_level,
            prepared_for_context=bool(step.get("prepared_for_context")),
            prepared_for_execution=bool(step.get("prepared_for_execution")),
            preflight_results=[result.to_payload() for result in preflight_results],
            warnings=warnings,
            approval_needed=approval_needed,
            failure_reason=(
                "Required preflight checks failed: " + ", ".join(failed_required_preflights)
            ),
            summary_for_prompt=(
                f"{name}: blocked because required preflight failed "
                f"({', '.join(failed_required_preflights)})"
            ),
        )
    if approval_required_preflights:
        return SkillWorkerExecutionResult(
            step_id=str(step.get("step_id") or identifier),
            stage_name=str(step.get("stage_name") or "analysis"),
            name=name,
            role=SkillOrchestrationStepRole(str(step.get("role") or "supporting")),
            execution_intent=SkillExecutionIntent(
                str(step.get("execution_intent") or "prepare_context")
            ),
            status=SkillWorkerExecutionStatus.SKIPPED,
            skill_id=skill_id,
            duration_ms=int((perf_counter() - started_at) * 1000),
            trust_level=trust_level,
            prepared_for_context=bool(step.get("prepared_for_context")),
            prepared_for_execution=bool(step.get("prepared_for_execution")),
            preflight_results=[result.to_payload() for result in preflight_results],
            warnings=warnings,
            approval_needed=True,
            failure_reason=(
                "Required preflight checks need approval: "
                + ", ".join(approval_required_preflights)
            ),
            summary_for_prompt=(
                f"{name}: blocked pending approval for {', '.join(approval_required_preflights)}"
            ),
        )
    try:
        execution_output = execute_skill_facade(
            identifier,
            arguments=arguments,
            workspace_path=workspace_path,
            touched_paths=touched_paths,
            session_id=session_id,
        )
    except Exception as exc:  # noqa: BLE001
        return SkillWorkerExecutionResult(
            step_id=str(step.get("step_id") or identifier),
            stage_name=str(step.get("stage_name") or "analysis"),
            name=name,
            role=SkillOrchestrationStepRole(str(step.get("role") or "supporting")),
            execution_intent=SkillExecutionIntent(
                str(step.get("execution_intent") or "prepare_context")
            ),
            status=SkillWorkerExecutionStatus.FAILED,
            skill_id=skill_id,
            duration_ms=int((perf_counter() - started_at) * 1000),
            trust_level=trust_level,
            prepared_for_context=bool(step.get("prepared_for_context")),
            prepared_for_execution=bool(step.get("prepared_for_execution")),
            preflight_results=[result.to_payload() for result in preflight_results],
            warnings=warnings,
            approval_needed=approval_needed,
            failure_reason=str(exc),
            summary_for_prompt=f"{name}: failed ({exc})",
        )

    pending_shell_approval = _has_pending_shell_approval(execution_output)
    approval_needed = approval_needed or pending_shell_approval
    summary_parts = [name, "status=prepared"]
    if approval_needed:
        summary_parts.append("approval_needed=true")
    return SkillWorkerExecutionResult(
        step_id=str(step.get("step_id") or identifier),
        stage_name=str(step.get("stage_name") or "analysis"),
        name=name,
        role=SkillOrchestrationStepRole(str(step.get("role") or "supporting")),
        execution_intent=SkillExecutionIntent(
            str(step.get("execution_intent") or "prepare_context")
        ),
        status=SkillWorkerExecutionStatus.SUCCEEDED,
        skill_id=skill_id,
        duration_ms=int((perf_counter() - started_at) * 1000),
        trust_level=trust_level,
        prepared_for_context=bool(step.get("prepared_for_context")),
        prepared_for_execution=bool(step.get("prepared_for_execution")),
        preflight_results=[result.to_payload() for result in preflight_results],
        execution_output=cast(dict[str, object], execution_output),
        warnings=warnings,
        approval_needed=approval_needed,
        summary_for_prompt="; ".join(summary_parts),
    )


def _run_preflight_checks(
    raw_checks: list[dict[str, object]], *, trust_level: str | None, workspace_path: str | None
) -> list[SkillPreflightResult]:
    checks = [_coerce_preflight_check(item) for item in raw_checks if isinstance(item, dict)]
    results: list[SkillPreflightResult] = []
    for check in checks:
        if not can_auto_run_preflight(check, trust_level=trust_level):
            results.append(
                SkillPreflightResult(
                    name=check.name,
                    kind=check.kind,
                    status=SkillPreflightResultStatus.APPROVAL_REQUIRED,
                    required=check.required,
                    read_only=check.read_only,
                    metadata=check.metadata,
                    warning="Preflight stays approval-gated for this trust profile or kind.",
                )
            )
            continue
        try:
            output_summary = _auto_run_preflight_check(check, workspace_path=workspace_path)
            results.append(
                SkillPreflightResult(
                    name=check.name,
                    kind=check.kind,
                    status=SkillPreflightResultStatus.SUCCEEDED,
                    required=check.required,
                    read_only=check.read_only,
                    auto_ran=True,
                    output_summary=output_summary,
                    metadata=check.metadata,
                )
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                SkillPreflightResult(
                    name=check.name,
                    kind=check.kind,
                    status=SkillPreflightResultStatus.FAILED,
                    required=check.required,
                    read_only=check.read_only,
                    auto_ran=True,
                    error=str(exc),
                    metadata=check.metadata,
                )
            )
    return results


def _coerce_preflight_check(payload: dict[str, object]) -> SkillPreflightCheck:
    return SkillPreflightCheck(
        name=str(payload.get("name") or "preflight"),
        kind=str(payload.get("kind") or payload.get("name") or "generic"),
        required=bool(payload.get("required", True)),
        read_only=bool(payload.get("read_only", True)),
        description=cast(str | None, payload.get("description")),
        metadata=cast(dict[str, object], payload.get("metadata", {})),
    )


def _auto_run_preflight_check(check: SkillPreflightCheck, *, workspace_path: str | None) -> str:
    normalized_kind = normalize_preflight_kind(check)
    workspace = Path(workspace_path).resolve() if workspace_path else Path.cwd().resolve()
    if normalized_kind in {"pwd", "cwd", "workspace"}:
        return str(workspace)
    if normalized_kind in {"list_dir", "ls", "directory"}:
        target = _safe_workspace_path(workspace, cast(str | None, check.metadata.get("path")))
        limit = _bounded_int(check.metadata.get("limit"), default=10, minimum=1, maximum=25)
        entries = [entry.name for entry in sorted(target.iterdir())[:limit]]
        return ", ".join(entries)
    if normalized_kind in {"git_status", "repo_state"}:
        return _run_git_command(workspace, ["status", "--short"]) or "clean"
    if normalized_kind == "git_diff_stat":
        return _run_git_command(workspace, ["diff", "--stat"]) or "no diff"
    if normalized_kind == "git_log":
        limit = _bounded_int(check.metadata.get("limit"), default=3, minimum=1, maximum=10)
        return _run_git_command(workspace, ["log", "--oneline", f"-n{limit}"])
    if normalized_kind == "file_preview":
        target = _safe_workspace_path(workspace, cast(str | None, check.metadata.get("path")))
        limit = _bounded_int(check.metadata.get("lines"), default=5, minimum=1, maximum=20)
        lines = target.read_text(encoding="utf-8").splitlines()[:limit]
        return " | ".join(lines)
    raise ValueError(f"Unsupported auto-runnable preflight kind: {normalized_kind}")


def _safe_workspace_path(workspace: Path, relative_path: str | None) -> Path:
    if not relative_path:
        return workspace
    candidate = (workspace / relative_path).resolve()
    candidate.relative_to(workspace)
    return candidate


def _run_git_command(workspace: Path, args: list[str]) -> str:
    completed = run(
        ["git", *args],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        check=False,
    )
    output = completed.stdout.strip()
    if completed.returncode != 0:
        raise ValueError(completed.stderr.strip() or "git preflight command failed")
    return output


def _bounded_int(
    raw_value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if not isinstance(raw_value, int):
        return default
    return max(minimum, min(raw_value, maximum))


def _build_reduction_result(
    stage_payload: dict[str, object],
    worker_results: list[SkillWorkerExecutionResult],
) -> SkillReductionResult | None:
    if not any(
        isinstance(step, dict) and step.get("role") == SkillOrchestrationStepRole.REDUCER.value
        for step in cast(list[dict[str, object]], stage_payload.get("steps", []))
    ):
        return None
    return reduce_stage_results(
        stage_name=str(stage_payload.get("stage_name") or "analysis"),
        worker_results=worker_results,
    )


def _has_pending_shell_approval(execution_output: dict[str, Any]) -> bool:
    execution = execution_output.get("execution")
    if not isinstance(execution, dict):
        return False
    prepared_invocation = execution.get("prepared_invocation")
    if not isinstance(prepared_invocation, dict):
        return False
    pending_actions = prepared_invocation.get("pending_actions")
    if not isinstance(pending_actions, list):
        return False
    return any(
        isinstance(action, dict)
        and action.get("action_type") == "shell_expansion"
        and action.get("status") == "pending_approval"
        for action in pending_actions
    )


def _skipped_worker_result(step: dict[str, object]) -> SkillWorkerExecutionResult:
    return SkillWorkerExecutionResult(
        step_id=str(step.get("step_id") or "skipped"),
        stage_name=str(step.get("stage_name") or "analysis"),
        name=str(
            step.get("name") or step.get("directory_name") or step.get("skill_id") or "unknown"
        ),
        role=SkillOrchestrationStepRole(str(step.get("role") or "supporting")),
        execution_intent=SkillExecutionIntent(
            str(step.get("execution_intent") or "prepare_context")
        ),
        status=SkillWorkerExecutionStatus.SKIPPED,
        skill_id=cast(str | None, step.get("skill_id")),
        trust_level=cast(str | None, step.get("trust_level")),
        prepared_for_context=bool(step.get("prepared_for_context")),
        prepared_for_execution=bool(step.get("prepared_for_execution")),
        required=False,
        summary_for_prompt=(
            f"{step.get('name') or step.get('directory_name') or step.get('skill_id')}: skipped"
        ),
    )
