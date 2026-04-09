from __future__ import annotations

from collections import deque
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path
from subprocess import run
from time import perf_counter
from typing import Any, Protocol, cast

from .orchestration_models import (
    SkillExecutionIntent,
    SkillOrchestrationExecutionResult,
    SkillOrchestrationStepRole,
    SkillReductionResult,
    SkillReductionStatus,
    SkillVerificationResult,
    SkillVerificationStatus,
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
    executable_steps = [step for step in steps if _is_skill_worker_step(step)]
    failure_policy = cast(str | None, stage_payload.get("failure_policy"))
    fail_fast = failure_policy == "fail_fast"
    max_parallel_workers = _max_worker_count(stage_payload, executable_steps)
    orchestration_timeout_ms = _optional_int(stage_payload.get("orchestration_timeout_ms"))
    orchestration_deadline = (
        None if orchestration_timeout_ms is None else started_at + (orchestration_timeout_ms / 1000)
    )
    attempts: dict[str, int] = {}
    node_results: dict[str, SkillWorkerExecutionResult] = {}
    pending_steps = deque(executable_steps)
    running: dict[Future[SkillWorkerExecutionResult], tuple[dict[str, object], float]] = {}
    warnings: list[str] = []
    cancelled = False
    timed_out = False
    cancellation_reason: str | None = None
    executor = ThreadPoolExecutor(max_workers=max_parallel_workers)

    try:
        while pending_steps or running:
            now = perf_counter()
            if orchestration_deadline is not None and now >= orchestration_deadline:
                cancelled = True
                timed_out = True
                cancellation_reason = "orchestration_timeout"
                warnings.append("Orchestration timeout reached before all worker steps completed.")
                break

            while pending_steps and len(running) < max_parallel_workers:
                step = pending_steps.popleft()
                step_id = str(step.get("step_id") or "unknown-step")
                attempts[step_id] = attempts.get(step_id, 0) + 1
                future = executor.submit(
                    _execute_worker_step,
                    step=step,
                    execute_skill_facade=execute_skill_facade,
                    arguments=arguments,
                    workspace_path=workspace_path,
                    touched_paths=touched_paths,
                    session_id=session_id,
                    timeout_ms=_worker_timeout_ms(step, stage_payload),
                    attempt_count=attempts[step_id],
                )
                running[future] = (step, perf_counter())

            if not running:
                continue

            wait_timeout = _next_wait_timeout(
                stage_payload=stage_payload,
                running=running,
                orchestration_deadline=orchestration_deadline,
            )
            done, _ = wait(
                running.keys(),
                timeout=wait_timeout,
                return_when=FIRST_COMPLETED,
            )

            if not done:
                timed_out_steps = _collect_timed_out_steps(
                    running,
                    stage_payload=stage_payload,
                    attempts=attempts,
                )
                if timed_out_steps:
                    for future, result in timed_out_steps:
                        step, _ = running.pop(future)
                        _handle_completed_result(
                            result=result,
                            step=step,
                            stage_payload=stage_payload,
                            pending_steps=pending_steps,
                            node_results=node_results,
                            warnings=warnings,
                            attempts=attempts,
                        )
                        if fail_fast and _is_fail_fast_stop(result):
                            cancelled = True
                            cancellation_reason = result.failure_reason or "fail_fast_timeout"
                            break
                if cancelled:
                    break
                continue

            for future in done:
                step, _ = running.pop(future)
                result = future.result()
                _handle_completed_result(
                    result=result,
                    step=step,
                    stage_payload=stage_payload,
                    pending_steps=pending_steps,
                    node_results=node_results,
                    warnings=warnings,
                    attempts=attempts,
                )
                if fail_fast and _is_fail_fast_stop(result):
                    cancelled = True
                    cancellation_reason = (
                        result.failure_reason or result.summary_for_prompt or "fail_fast"
                    )
                    break
            if cancelled:
                break
    finally:
        if cancelled:
            _cancel_outstanding_steps(
                pending_steps=pending_steps,
                running=running,
                node_results=node_results,
                cancellation_reason=cancellation_reason or "cancelled",
            )
        executor.shutdown(wait=False, cancel_futures=True)

    reduction_result, reduction_node_result = _build_reduction_result(stage_payload, node_results)
    verification_result, verifier_node_result = _build_verification_result(
        stage_payload,
        failure_policy=failure_policy,
        node_results=node_results,
    )
    if reduction_node_result is not None:
        node_results[reduction_node_result.step_id] = reduction_node_result
    if verifier_node_result is not None:
        node_results[verifier_node_result.step_id] = verifier_node_result

    ordered_results = [
        node_results.get(cast(str, step.get("step_id"))) or _skipped_step_result(step)
        for step in steps
        if isinstance(step, dict)
    ]
    effective_verification = verification_result or SkillVerificationResult(
        stage_name=str(stage_payload.get("stage_name") or active_stage or "analysis"),
        status=SkillVerificationStatus.SKIPPED,
        passed=True,
    )
    stage_transition = build_stage_transition(
        active_stage=active_stage,
        replan_triggers=cast(list[str], orchestration_plan.get("replan_triggers", [])),
        verification_result=effective_verification,
    )
    duration_ms = int((perf_counter() - started_at) * 1000)
    status = "completed" if effective_verification.passed and not timed_out else "failed"
    if cancelled and not timed_out:
        status = "cancelled"
    return SkillOrchestrationExecutionResult(
        active_stage=active_stage,
        mode=cast(str | None, stage_payload.get("mode")),
        failure_policy=failure_policy,
        status=status,
        duration_ms=duration_ms,
        worker_results=ordered_results,
        node_results=ordered_results,
        reduction_result=reduction_result,
        verification_result=verification_result,
        stage_transition=stage_transition,
        orchestration_timeout_ms=orchestration_timeout_ms,
        cancelled=cancelled,
        timed_out=timed_out,
        warnings=[warning for result in ordered_results for warning in result.warnings] + warnings,
        notes=[
            "Execution stays above fixed skill facades and preserves public payload semantics.",
        ],
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


def _is_skill_worker_step(step: dict[str, object]) -> bool:
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
    timeout_ms: int | None,
    attempt_count: int,
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
        return _terminal_worker_result(
            step=step,
            status=SkillWorkerExecutionStatus.FAILED,
            name=name,
            skill_id=skill_id,
            trust_level=trust_level,
            preflight_results=preflight_results,
            warnings=warnings,
            duration_ms=int((perf_counter() - started_at) * 1000),
            attempt_count=attempt_count,
            timeout_ms=timeout_ms,
            failure_reason="Required preflight checks failed: "
            + ", ".join(failed_required_preflights),
            summary_for_prompt=(
                f"{name}: blocked because required preflight failed "
                f"({', '.join(failed_required_preflights)})"
            ),
        )
    if approval_required_preflights:
        return _terminal_worker_result(
            step=step,
            status=SkillWorkerExecutionStatus.SKIPPED,
            name=name,
            skill_id=skill_id,
            trust_level=trust_level,
            preflight_results=preflight_results,
            warnings=warnings,
            approval_needed=True,
            duration_ms=int((perf_counter() - started_at) * 1000),
            attempt_count=attempt_count,
            timeout_ms=timeout_ms,
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
        return _terminal_worker_result(
            step=step,
            status=SkillWorkerExecutionStatus.FAILED,
            name=name,
            skill_id=skill_id,
            trust_level=trust_level,
            preflight_results=preflight_results,
            warnings=warnings,
            duration_ms=int((perf_counter() - started_at) * 1000),
            attempt_count=attempt_count,
            timeout_ms=timeout_ms,
            failure_reason=str(exc),
            summary_for_prompt=f"{name}: failed ({exc})",
        )

    approval_needed = _has_pending_shell_approval(execution_output)
    summary_parts = [name, "status=prepared"]
    if approval_needed:
        summary_parts.append("approval_needed=true")
    return _terminal_worker_result(
        step=step,
        status=SkillWorkerExecutionStatus.SUCCEEDED,
        name=name,
        skill_id=skill_id,
        trust_level=trust_level,
        preflight_results=preflight_results,
        warnings=warnings,
        approval_needed=approval_needed,
        duration_ms=int((perf_counter() - started_at) * 1000),
        attempt_count=attempt_count,
        timeout_ms=timeout_ms,
        execution_output=cast(dict[str, object], execution_output),
        summary_for_prompt="; ".join(summary_parts),
    )


def _terminal_worker_result(
    *,
    step: dict[str, object],
    status: SkillWorkerExecutionStatus,
    name: str,
    skill_id: str | None,
    trust_level: str | None,
    preflight_results: list[SkillPreflightResult],
    warnings: list[str],
    duration_ms: int,
    attempt_count: int,
    timeout_ms: int | None,
    approval_needed: bool = False,
    execution_output: dict[str, object] | None = None,
    failure_reason: str | None = None,
    summary_for_prompt: str | None = None,
    cancelled: bool = False,
    timed_out: bool = False,
    cancellation_reason: str | None = None,
) -> SkillWorkerExecutionResult:
    return SkillWorkerExecutionResult(
        step_id=str(step.get("step_id") or skill_id or name),
        stage_name=str(step.get("stage_name") or "analysis"),
        name=name,
        role=SkillOrchestrationStepRole(str(step.get("role") or "supporting")),
        execution_intent=SkillExecutionIntent(
            str(step.get("execution_intent") or "prepare_context")
        ),
        status=status,
        skill_id=skill_id,
        node_kind=str(step.get("node_kind") or "skill"),
        internal_node=bool(step.get("internal_node")),
        duration_ms=duration_ms,
        trust_level=trust_level,
        version=cast(str | None, step.get("version")),
        model_hint=cast(str | None, step.get("model_hint")),
        prepared_for_context=bool(step.get("prepared_for_context")),
        prepared_for_execution=bool(step.get("prepared_for_execution")),
        required=bool(step.get("required", True)),
        preflight_results=[result.to_payload() for result in preflight_results],
        execution_output=execution_output,
        warnings=warnings,
        approval_needed=approval_needed,
        attempt_count=attempt_count,
        retry_count=max(0, attempt_count - 1),
        timeout_ms=timeout_ms,
        cancelled=cancelled,
        timed_out=timed_out,
        cancellation_reason=cancellation_reason,
        failure_reason=failure_reason,
        summary_for_prompt=summary_for_prompt,
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
    node_results: dict[str, SkillWorkerExecutionResult],
) -> tuple[SkillReductionResult | None, SkillWorkerExecutionResult | None]:
    reducer_step = _find_step(stage_payload, SkillOrchestrationStepRole.REDUCER.value)
    if reducer_step is None:
        return None, None
    reduction_result = reduce_stage_results(
        stage_name=str(stage_payload.get("stage_name") or "analysis"),
        worker_results=[result for result in node_results.values() if result.node_kind == "skill"],
    )
    reducer_result = SkillWorkerExecutionResult(
        step_id=str(reducer_step.get("step_id") or "reducer"),
        stage_name=str(reducer_step.get("stage_name") or "analysis"),
        name=str(reducer_step.get("name") or "reducer"),
        role=SkillOrchestrationStepRole.REDUCER,
        execution_intent=SkillExecutionIntent.REDUCE_RESULTS,
        status=(
            SkillWorkerExecutionStatus.SUCCEEDED
            if reduction_result.status is SkillReductionStatus.COMPLETED
            else SkillWorkerExecutionStatus.SKIPPED
        ),
        skill_id=cast(str | None, reducer_step.get("skill_id")),
        node_kind=str(reducer_step.get("node_kind") or "internal"),
        internal_node=True,
        trust_level=cast(str | None, reducer_step.get("trust_level")),
        prepared_for_context=bool(reducer_step.get("prepared_for_context")),
        prepared_for_execution=bool(reducer_step.get("prepared_for_execution")),
        summary_for_prompt=reduction_result.summary_for_prompt,
    )
    return reduction_result, reducer_result


def _build_verification_result(
    stage_payload: dict[str, object],
    *,
    failure_policy: str | None,
    node_results: dict[str, SkillWorkerExecutionResult],
) -> tuple[SkillVerificationResult | None, SkillWorkerExecutionResult | None]:
    verifier_step = _find_step(stage_payload, SkillOrchestrationStepRole.VERIFIER.value)
    verification_result = verify_stage_results(
        stage_name=str(stage_payload.get("stage_name") or "analysis"),
        failure_policy=failure_policy,
        worker_results=[result for result in node_results.values() if result.node_kind == "skill"],
    )
    if verifier_step is None:
        return verification_result, None
    verifier_result = SkillWorkerExecutionResult(
        step_id=str(verifier_step.get("step_id") or "verifier"),
        stage_name=str(verifier_step.get("stage_name") or "analysis"),
        name=str(verifier_step.get("name") or "verifier"),
        role=SkillOrchestrationStepRole.VERIFIER,
        execution_intent=SkillExecutionIntent.VERIFY_RESULTS,
        status=(
            SkillWorkerExecutionStatus.SUCCEEDED
            if verification_result.passed
            else SkillWorkerExecutionStatus.FAILED
        ),
        skill_id=cast(str | None, verifier_step.get("skill_id")),
        node_kind=str(verifier_step.get("node_kind") or "internal"),
        internal_node=True,
        trust_level=cast(str | None, verifier_step.get("trust_level")),
        prepared_for_context=bool(verifier_step.get("prepared_for_context")),
        prepared_for_execution=bool(verifier_step.get("prepared_for_execution")),
        warnings=list(verification_result.warnings),
        failure_reason=(
            None if verification_result.passed else "; ".join(verification_result.reasons)
        ),
        summary_for_prompt=(
            f"{verifier_step.get('name') or 'verifier'}: passed={verification_result.passed}"
        ),
    )
    return verification_result, verifier_result


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


def _skipped_step_result(step: dict[str, object]) -> SkillWorkerExecutionResult:
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
        node_kind=str(step.get("node_kind") or "skill"),
        internal_node=bool(step.get("internal_node")),
        trust_level=cast(str | None, step.get("trust_level")),
        version=cast(str | None, step.get("version")),
        model_hint=cast(str | None, step.get("model_hint")),
        prepared_for_context=bool(step.get("prepared_for_context")),
        prepared_for_execution=bool(step.get("prepared_for_execution")),
        required=False,
        summary_for_prompt=(
            f"{step.get('name') or step.get('directory_name') or step.get('skill_id')}: skipped"
        ),
    )


def _worker_timeout_ms(step: dict[str, object], stage_payload: dict[str, object]) -> int | None:
    policy = step.get("execution_policy")
    if isinstance(policy, dict):
        for key in ("timeout_ms", "worker_timeout_ms"):
            raw = policy.get(key)
            if isinstance(raw, int) and raw > 0:
                return raw
    raw_stage_timeout = stage_payload.get("worker_timeout_ms")
    return (
        raw_stage_timeout if isinstance(raw_stage_timeout, int) and raw_stage_timeout > 0 else None
    )


def _retry_limit(step: dict[str, object], stage_payload: dict[str, object]) -> int:
    policy = step.get("execution_policy")
    if isinstance(policy, dict):
        for key in ("retry_limit", "retry_count", "max_retries"):
            raw = policy.get(key)
            if isinstance(raw, int) and raw >= 0:
                return raw
    raw_stage_retry = stage_payload.get("retry_limit")
    return raw_stage_retry if isinstance(raw_stage_retry, int) and raw_stage_retry >= 0 else 0


def _should_retry_result(
    result: SkillWorkerExecutionResult,
    *,
    step: dict[str, object],
    stage_payload: dict[str, object],
    attempts: dict[str, int],
) -> bool:
    step_id = str(step.get("step_id") or "unknown-step")
    retry_limit = _retry_limit(step, stage_payload)
    if attempts.get(step_id, 0) > retry_limit:
        return False
    if result.approval_needed:
        return False
    if result.status in {
        SkillWorkerExecutionStatus.TIMED_OUT,
        SkillWorkerExecutionStatus.CANCELLED,
    }:
        return True
    if result.status is not SkillWorkerExecutionStatus.FAILED:
        return False
    failure_text = (result.failure_reason or "").casefold()
    return any(
        token in failure_text
        for token in ("timeout", "tempor", "rate limit", "unavailable", "connection")
    )


def _handle_completed_result(
    *,
    result: SkillWorkerExecutionResult,
    step: dict[str, object],
    stage_payload: dict[str, object],
    pending_steps: deque[dict[str, object]],
    node_results: dict[str, SkillWorkerExecutionResult],
    warnings: list[str],
    attempts: dict[str, int],
) -> None:
    step_id = str(step.get("step_id") or "unknown-step")
    if _should_retry_result(result, step=step, stage_payload=stage_payload, attempts=attempts):
        warnings.append(f"Retrying {result.name} after {result.status.value}.")
        pending_steps.appendleft(step)
        return
    node_results[step_id] = result


def _is_fail_fast_stop(result: SkillWorkerExecutionResult) -> bool:
    return result.approval_needed or result.status in {
        SkillWorkerExecutionStatus.FAILED,
        SkillWorkerExecutionStatus.TIMED_OUT,
        SkillWorkerExecutionStatus.CANCELLED,
    }


def _next_wait_timeout(
    *,
    stage_payload: dict[str, object],
    running: dict[Future[SkillWorkerExecutionResult], tuple[dict[str, object], float]],
    orchestration_deadline: float | None,
) -> float | None:
    timeouts = [
        timeout_ms
        for step, _started_at in running.values()
        if (timeout_ms := _worker_timeout_ms(step, stage_payload)) is not None
    ]
    wait_timeout = None if not timeouts else max(0.01, min(timeout / 1000 for timeout in timeouts))
    if orchestration_deadline is None:
        return wait_timeout
    remaining = max(0.01, orchestration_deadline - perf_counter())
    return remaining if wait_timeout is None else min(wait_timeout, remaining)


def _collect_timed_out_steps(
    running: dict[Future[SkillWorkerExecutionResult], tuple[dict[str, object], float]],
    *,
    stage_payload: dict[str, object],
    attempts: dict[str, int],
) -> list[tuple[Future[SkillWorkerExecutionResult], SkillWorkerExecutionResult]]:
    now = perf_counter()
    timed_out_results: list[
        tuple[Future[SkillWorkerExecutionResult], SkillWorkerExecutionResult]
    ] = []
    for future, (step, started_at) in list(running.items()):
        timeout_ms = _worker_timeout_ms(step, stage_payload)
        if timeout_ms is None or (now - started_at) < (timeout_ms / 1000):
            continue
        future.cancel()
        timed_out_results.append(
            (
                future,
                SkillWorkerExecutionResult(
                    step_id=str(step.get("step_id") or "timed-out"),
                    stage_name=str(step.get("stage_name") or "analysis"),
                    name=str(
                        step.get("name")
                        or step.get("directory_name")
                        or step.get("skill_id")
                        or "unknown"
                    ),
                    role=SkillOrchestrationStepRole(str(step.get("role") or "supporting")),
                    execution_intent=SkillExecutionIntent(
                        str(step.get("execution_intent") or "prepare_context")
                    ),
                    status=SkillWorkerExecutionStatus.TIMED_OUT,
                    skill_id=cast(str | None, step.get("skill_id")),
                    node_kind=str(step.get("node_kind") or "skill"),
                    internal_node=bool(step.get("internal_node")),
                    trust_level=cast(str | None, step.get("trust_level")),
                    version=cast(str | None, step.get("version")),
                    model_hint=cast(str | None, step.get("model_hint")),
                    prepared_for_context=bool(step.get("prepared_for_context")),
                    prepared_for_execution=bool(step.get("prepared_for_execution")),
                    attempt_count=attempts.get(str(step.get("step_id") or "timed-out"), 1),
                    retry_count=max(
                        0,
                        attempts.get(str(step.get("step_id") or "timed-out"), 1) - 1,
                    ),
                    timeout_ms=timeout_ms,
                    timed_out=True,
                    failure_reason=f"Worker exceeded timeout of {timeout_ms}ms.",
                    summary_for_prompt=(
                        f"{step.get('name') or step.get('skill_id')}: timed out after "
                        f"{timeout_ms}ms"
                    ),
                ),
            )
        )
    return timed_out_results


def _cancel_outstanding_steps(
    *,
    pending_steps: deque[dict[str, object]],
    running: dict[Future[SkillWorkerExecutionResult], tuple[dict[str, object], float]],
    node_results: dict[str, SkillWorkerExecutionResult],
    cancellation_reason: str,
) -> None:
    for step in list(pending_steps):
        step_id = str(step.get("step_id") or "cancelled")
        if step_id in node_results:
            continue
        node_results[step_id] = SkillWorkerExecutionResult(
            step_id=step_id,
            stage_name=str(step.get("stage_name") or "analysis"),
            name=str(
                step.get("name") or step.get("directory_name") or step.get("skill_id") or "unknown"
            ),
            role=SkillOrchestrationStepRole(str(step.get("role") or "supporting")),
            execution_intent=SkillExecutionIntent(
                str(step.get("execution_intent") or "prepare_context")
            ),
            status=SkillWorkerExecutionStatus.CANCELLED,
            skill_id=cast(str | None, step.get("skill_id")),
            node_kind=str(step.get("node_kind") or "skill"),
            internal_node=bool(step.get("internal_node")),
            trust_level=cast(str | None, step.get("trust_level")),
            version=cast(str | None, step.get("version")),
            model_hint=cast(str | None, step.get("model_hint")),
            prepared_for_context=bool(step.get("prepared_for_context")),
            prepared_for_execution=bool(step.get("prepared_for_execution")),
            cancelled=True,
            cancellation_reason=cancellation_reason,
            failure_reason=cancellation_reason,
            summary_for_prompt=(
                f"{step.get('name') or step.get('skill_id')}: cancelled ({cancellation_reason})"
            ),
        )
    for future, (step, _started_at) in list(running.items()):
        step_id = str(step.get("step_id") or "cancelled")
        if step_id in node_results:
            continue
        future.cancel()
        node_results[step_id] = SkillWorkerExecutionResult(
            step_id=step_id,
            stage_name=str(step.get("stage_name") or "analysis"),
            name=str(
                step.get("name") or step.get("directory_name") or step.get("skill_id") or "unknown"
            ),
            role=SkillOrchestrationStepRole(str(step.get("role") or "supporting")),
            execution_intent=SkillExecutionIntent(
                str(step.get("execution_intent") or "prepare_context")
            ),
            status=SkillWorkerExecutionStatus.CANCELLED,
            skill_id=cast(str | None, step.get("skill_id")),
            node_kind=str(step.get("node_kind") or "skill"),
            internal_node=bool(step.get("internal_node")),
            trust_level=cast(str | None, step.get("trust_level")),
            version=cast(str | None, step.get("version")),
            model_hint=cast(str | None, step.get("model_hint")),
            prepared_for_context=bool(step.get("prepared_for_context")),
            prepared_for_execution=bool(step.get("prepared_for_execution")),
            cancelled=True,
            cancellation_reason=cancellation_reason,
            failure_reason=cancellation_reason,
            summary_for_prompt=(
                f"{step.get('name') or step.get('skill_id')}: cancelled ({cancellation_reason})"
            ),
        )


def _optional_int(raw_value: object) -> int | None:
    return raw_value if isinstance(raw_value, int) and raw_value > 0 else None


def _find_step(stage_payload: dict[str, object], role: str) -> dict[str, object] | None:
    steps = stage_payload.get("steps")
    if not isinstance(steps, list):
        return None
    for step in steps:
        if isinstance(step, dict) and step.get("role") == role:
            return step
    return None
