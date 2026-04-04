from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from app.agent.compact_runtime import CompactRuntimeService
from app.agent.context_models import ContextSnapshot
from app.agent.context_projection import ContextProjectionBuilder
from app.agent.executor import ExecutionResult, Executor
from app.agent.loop_models import LoopSelectedTask, WorkflowCycleArtifact, WorkflowLoopState
from app.agent.memory import MemoryManager
from app.agent.pause_runtime import PauseRuntimeService
from app.agent.post_compact_reinjection import PostCompactReinjectionService
from app.agent.prompting import build_workflow_prompting_state
from app.agent.reflector import ReflectionResult, Reflector
from app.agent.retrieval import RetrievalPipeline
from app.agent.selection import RunnableSelection, WorkflowRunnableSelector
from app.agent.tool_scheduler import (
    WorkflowToolSchedule,
    WorkflowToolScheduler,
    build_scheduler_summary,
)
from app.agent.transcript_runtime import TranscriptRuntimeService
from app.agent.turn_models import NextTurnDirective
from app.agent.turn_planner import AssistantTurnPlanner
from app.agent.workflow import WorkflowExecutionContext, WorkflowGraphRuntime
from app.db.models import Session, TaskNode, TaskNodeStatus, WorkflowRun, WorkflowRunStatus
from app.db.repositories import GraphRepository, RunLogRepository, SessionRepository
from app.services.capabilities import CapabilityFacade


@dataclass(frozen=True)
class LoopAdvanceResult:
    status: WorkflowRunStatus
    current_stage: str | None
    state: dict[str, object]
    last_error: str | None
    ended_at: datetime | None
    approval_required: bool
    executed_task_id: str | None
    executed_task_ids: list[str]


@dataclass(frozen=True)
class _TaskExecutionOutcome:
    task: TaskNode
    execution: ExecutionResult
    reflection: ReflectionResult
    merge_candidate: _ConcurrentToolPayload


@dataclass(frozen=True)
class _ConcurrentToolPayload:
    tool_result: dict[str, object]
    context_modifier: dict[str, object]
    citations: list[dict[str, object]]
    artifacts: list[dict[str, object]]
    trace: dict[str, object]


class WorkflowLoopEngine:
    def __init__(
        self,
        *,
        executor: Executor,
        reflector: Reflector,
        max_active_execution_records: int,
        max_active_messages: int,
        session_repository: SessionRepository,
        run_log_repository: RunLogRepository,
        graph_repository: GraphRepository,
        capability_facade: CapabilityFacade,
        selector: WorkflowRunnableSelector | None = None,
        runtime: WorkflowGraphRuntime | None = None,
    ) -> None:
        self._executor = executor
        self._reflector = reflector
        self._max_active_execution_records = max_active_execution_records
        self._max_active_messages = max_active_messages
        self._session_repository = session_repository
        self._graph_repository = graph_repository
        self._capability_facade = capability_facade
        self._retrieval_pipeline = RetrievalPipeline(run_log_repository=run_log_repository)
        self._memory_manager = MemoryManager()
        self._pause_runtime = PauseRuntimeService()
        self._context_projection_builder = ContextProjectionBuilder()
        self._compact_runtime = CompactRuntimeService()
        self._post_compact_reinjection = PostCompactReinjectionService()
        self._transcript_runtime = TranscriptRuntimeService()
        self._assistant_turn_planner = AssistantTurnPlanner()
        self._runtime = runtime or WorkflowGraphRuntime()
        self._selector = selector or WorkflowRunnableSelector(
            self._runtime,
            self._executor.resolve_tool_spec,
        )
        self._tool_scheduler = WorkflowToolScheduler()

    async def advance(
        self,
        *,
        run: WorkflowRun,
        tasks: list[TaskNode],
        approve: bool,
        user_input: str | None,
        resume_token: str | None,
        resolution_payload: dict[str, object] | None,
    ) -> LoopAdvanceResult:
        mutable_state = dict(run.state_json)
        self._transcript_runtime.ensure_state(mutable_state)
        self._pause_runtime.ensure_state(mutable_state)
        self._ensure_batch_contract(mutable_state)
        loop_state = WorkflowLoopState.from_state(mutable_state)
        session = self._session_repository.get_session(run.session_id)
        self._runtime.materialize_ready_tasks(tasks)
        resolved_protocol = self._pause_runtime.resolve_pending(
            mutable_state=mutable_state,
            approve=approve,
            user_input=user_input,
            resume_token=resume_token,
            resolution_payload=resolution_payload,
        )
        if isinstance(resolved_protocol, dict):
            resolved_task = self._pause_runtime.mark_task_ready_for_resolution(
                tasks=tasks,
                resolved_entry=resolved_protocol,
            )
            self._sync_compatibility_approval_state(
                mutable_state=mutable_state,
                resolved_protocol=resolved_protocol,
                resolved_task=resolved_task,
            )
            self._transcript_runtime.append_protocol_resolution_event(
                mutable_state=mutable_state,
                cycle_id=f"cycle-{self._batch_cycle(mutable_state)}",
                current_stage=(
                    self._runtime.task_stage(resolved_task) if resolved_task else run.current_stage
                ),
                task_name=resolved_task.name if resolved_task is not None else "workflow-protocol",
                resolved_entry=resolved_protocol,
            )
            self._transcript_runtime.set_last_directive(
                mutable_state,
                NextTurnDirective.CONTINUE,
            )
        context_snapshot = self._build_context_snapshot(
            run=run,
            session=session,
            mutable_state=mutable_state,
            tasks=tasks,
        )

        active_pending = self._pause_runtime.active_pending(mutable_state)
        if isinstance(active_pending, dict):
            return self._return_pending_protocol_pause(
                run=run,
                tasks=tasks,
                mutable_state=mutable_state,
                loop_state=loop_state,
                context_snapshot=context_snapshot,
                pending_entry=active_pending,
            )

        blocked_approval_tasks = self._runtime.blocked_for_approval(tasks)
        if blocked_approval_tasks and not approve:
            pending_task = blocked_approval_tasks[0]
            current_stage = self._runtime.task_stage(pending_task)
            mutable_state["approval"] = {"required": True, "pending_task_id": pending_task.id}
            mutable_state["current_stage"] = current_stage
            self._update_batch_state(
                mutable_state,
                status="waiting_approval",
                selected_task_ids=[],
                executed_task_ids=[],
            )
            idle_schedule = self._tool_scheduler.build_schedule(
                RunnableSelection(
                    batch_size=self._runtime.resolve_batch_size(mutable_state),
                    selected_tasks=[],
                )
            )
            loop_state = loop_state.append_cycle(
                self._build_cycle_artifact(
                    mutable_state,
                    schedule=idle_schedule,
                    context_snapshot=context_snapshot,
                    tool_results=[],
                    reflection_summaries=[],
                    merge_summary=self._build_merge_summary(
                        merge_events=[],
                        executed_task_ids=[],
                        partial_failures=[],
                    ),
                    partial_failures=[],
                    next_action=self._transcript_runtime.directive_to_next_action(
                        self._transcript_runtime.set_last_directive(
                            mutable_state,
                            self._transcript_runtime.directive_for_run_status(
                                WorkflowRunStatus.NEEDS_APPROVAL
                            ),
                        )
                    ),
                )
            )
            loop_state.apply_to_state(mutable_state)
            return LoopAdvanceResult(
                status=WorkflowRunStatus.NEEDS_APPROVAL,
                current_stage=current_stage,
                state=mutable_state,
                last_error=None,
                ended_at=None,
                approval_required=True,
                executed_task_id=None,
                executed_task_ids=[],
            )

        if blocked_approval_tasks and approve:
            blocked_approval_tasks[0].status = TaskNodeStatus.READY
            self._runtime.sync_execution_state(blocked_approval_tasks[0])
            mutable_state["approval"] = {"required": False, "pending_task_id": None}
            self._transcript_runtime.set_last_directive(
                mutable_state,
                NextTurnDirective.CONTINUE,
            )

        selection = self._selector.select(tasks=tasks, state=mutable_state)
        schedule = self._tool_scheduler.build_schedule(selection)
        task_by_id = {task.id: task for task in tasks}
        runnable_tasks = [
            task_by_id[selected_task.task_id]
            for selected_task in selection.selected_tasks
            if selected_task.task_id in task_by_id
        ]
        if not runnable_tasks:
            resolved_status = self._runtime.resolve_run_status(tasks)
            ended_at = datetime.now(UTC) if resolved_status is WorkflowRunStatus.DONE else None
            mutable_state["approval"] = {"required": False, "pending_task_id": None}
            self._update_batch_state(
                mutable_state,
                status="idle",
                selected_task_ids=[],
                executed_task_ids=[],
            )
            loop_state = loop_state.append_cycle(
                self._build_cycle_artifact(
                    mutable_state,
                    schedule=schedule,
                    context_snapshot=context_snapshot,
                    tool_results=[],
                    reflection_summaries=[],
                    merge_summary=self._build_merge_summary(
                        merge_events=[],
                        executed_task_ids=[],
                        partial_failures=[],
                    ),
                    partial_failures=[],
                    next_action=self._transcript_runtime.directive_to_next_action(
                        self._transcript_runtime.set_last_directive(
                            mutable_state,
                            self._transcript_runtime.directive_for_run_status(resolved_status),
                        )
                    ),
                )
            )
            loop_state.apply_to_state(mutable_state)
            return LoopAdvanceResult(
                status=resolved_status,
                current_stage=run.current_stage,
                state=mutable_state,
                last_error=run.last_error,
                ended_at=ended_at,
                approval_required=False,
                executed_task_id=None,
                executed_task_ids=[],
            )

        self._start_batch_cycle(mutable_state, selected_task_ids=selection.selected_task_ids)
        context_snapshot = self._build_context_snapshot(
            run=run,
            session=session,
            mutable_state=mutable_state,
            tasks=tasks,
        )
        executed_task_ids: list[str] = []
        tool_results: list[dict[str, object]] = []
        reflection_summaries: list[str] = []
        last_executed_task: TaskNode | None = None
        last_failure_reason: str | None = None
        merge_events: list[dict[str, object]] = []
        partial_failures: list[dict[str, object]] = []
        latest_directive = self._transcript_runtime.last_directive(mutable_state)

        for phase in schedule.phases:
            phase_tasks = [
                task_by_id[selected_task.task_id]
                for selected_task in phase.tasks
                if selected_task.task_id in task_by_id
            ]
            if not phase_tasks:
                continue

            if phase.scheduler_group == "parallel_read_group":
                blocked_index = next(
                    (
                        index
                        for index, phase_task in enumerate(phase_tasks)
                        if (
                            self._runtime.approval_required(phase_task)
                            and not approve
                            and self._executor.resolve_tool_spec(phase_task).name
                            != "workflow.request_approval"
                        )
                    ),
                    None,
                )
                runnable_read_tasks = (
                    phase_tasks[:blocked_index] if isinstance(blocked_index, int) else phase_tasks
                )
                if runnable_read_tasks:
                    read_outcomes = await self._execute_parallel_read_wave(
                        run=run,
                        session=session,
                        mutable_state=mutable_state,
                        context_snapshot=context_snapshot,
                        read_tasks=runnable_read_tasks,
                    )
                    merge_result = self._merge_execution_outcomes(
                        run=run,
                        outcomes=read_outcomes,
                        mutable_state=mutable_state,
                        tasks=tasks,
                        tool_results=tool_results,
                        reflection_summaries=reflection_summaries,
                        executed_task_ids=executed_task_ids,
                        scheduler_group=phase.scheduler_group,
                    )
                    latest_directive = self._directive_from_merge_result(
                        merge_result, latest_directive
                    )
                    self._transcript_runtime.set_last_directive(mutable_state, latest_directive)
                    merge_events.append(merge_result)
                    merge_partial_failures = merge_result.get("partial_failures")
                    if isinstance(merge_partial_failures, list):
                        partial_failures.extend(
                            [
                                dict(item)
                                for item in merge_partial_failures
                                if isinstance(item, dict)
                            ]
                        )
                    last_executed_task = read_outcomes[-1].task
                    failure_reason = self._last_failure_reason(read_outcomes)
                    if failure_reason is not None:
                        last_failure_reason = failure_reason
                    context_snapshot = self._build_context_snapshot(
                        run=run,
                        session=session,
                        mutable_state=mutable_state,
                        tasks=tasks,
                    )
                    directive_result = self._stop_after_directive(
                        run=run,
                        tasks=tasks,
                        mutable_state=mutable_state,
                        loop_state=loop_state,
                        schedule=schedule,
                        context_snapshot=context_snapshot,
                        tool_results=tool_results,
                        reflection_summaries=reflection_summaries,
                        merge_events=merge_events,
                        partial_failures=partial_failures,
                        executed_task_ids=executed_task_ids,
                        latest_directive=latest_directive,
                        last_executed_task=last_executed_task,
                        last_failure_reason=last_failure_reason,
                        blocked_task_id=self._blocked_task_id_from_merge_result(merge_result),
                    )
                    if directive_result is not None:
                        return directive_result
                if isinstance(blocked_index, int):
                    blocked_task = phase_tasks[blocked_index]
                    blocked_task.status = TaskNodeStatus.BLOCKED
                    self._runtime.sync_execution_state(blocked_task)
                    stage_key = self._runtime.task_stage(blocked_task)
                    mutable_state["approval"] = {
                        "required": True,
                        "pending_task_id": blocked_task.id,
                    }
                    mutable_state["current_stage"] = stage_key
                    self._update_batch_state(
                        mutable_state,
                        status="waiting_approval",
                        selected_task_ids=selection.selected_task_ids,
                        executed_task_ids=executed_task_ids,
                    )
                    context_snapshot = self._build_context_snapshot(
                        run=run,
                        session=session,
                        mutable_state=mutable_state,
                        tasks=tasks,
                    )
                    loop_state = loop_state.append_cycle(
                        self._build_cycle_artifact(
                            mutable_state,
                            schedule=schedule,
                            context_snapshot=context_snapshot,
                            tool_results=tool_results,
                            reflection_summaries=reflection_summaries,
                            merge_summary=self._build_merge_summary(
                                merge_events=merge_events,
                                executed_task_ids=executed_task_ids,
                                partial_failures=partial_failures,
                            ),
                            partial_failures=partial_failures,
                            next_action=self._transcript_runtime.directive_to_next_action(
                                self._transcript_runtime.set_last_directive(
                                    mutable_state,
                                    self._transcript_runtime.directive_for_run_status(
                                        WorkflowRunStatus.NEEDS_APPROVAL
                                    ),
                                )
                            ),
                        )
                    )
                    loop_state.apply_to_state(mutable_state)
                    return LoopAdvanceResult(
                        status=WorkflowRunStatus.NEEDS_APPROVAL,
                        current_stage=stage_key,
                        state=mutable_state,
                        last_error=None,
                        ended_at=None,
                        approval_required=True,
                        executed_task_id=(executed_task_ids[-1] if executed_task_ids else None),
                        executed_task_ids=executed_task_ids,
                    )
                continue

            for phase_task in phase_tasks:
                if (
                    self._runtime.approval_required(phase_task)
                    and not approve
                    and self._executor.resolve_tool_spec(phase_task).name
                    != "workflow.request_approval"
                ):
                    phase_task.status = TaskNodeStatus.BLOCKED
                    self._runtime.sync_execution_state(phase_task)
                    stage_key = self._runtime.task_stage(phase_task)
                    mutable_state["approval"] = {"required": True, "pending_task_id": phase_task.id}
                    mutable_state["current_stage"] = stage_key
                    self._update_batch_state(
                        mutable_state,
                        status="waiting_approval",
                        selected_task_ids=selection.selected_task_ids,
                        executed_task_ids=executed_task_ids,
                    )
                    context_snapshot = self._build_context_snapshot(
                        run=run,
                        session=session,
                        mutable_state=mutable_state,
                        tasks=tasks,
                    )
                    loop_state = loop_state.append_cycle(
                        self._build_cycle_artifact(
                            mutable_state,
                            schedule=schedule,
                            context_snapshot=context_snapshot,
                            tool_results=tool_results,
                            reflection_summaries=reflection_summaries,
                            merge_summary=self._build_merge_summary(
                                merge_events=merge_events,
                                executed_task_ids=executed_task_ids,
                                partial_failures=partial_failures,
                            ),
                            partial_failures=partial_failures,
                            next_action=self._transcript_runtime.directive_to_next_action(
                                self._transcript_runtime.set_last_directive(
                                    mutable_state,
                                    self._transcript_runtime.directive_for_run_status(
                                        WorkflowRunStatus.NEEDS_APPROVAL
                                    ),
                                )
                            ),
                        )
                    )
                    loop_state.apply_to_state(mutable_state)
                    return LoopAdvanceResult(
                        status=WorkflowRunStatus.NEEDS_APPROVAL,
                        current_stage=stage_key,
                        state=mutable_state,
                        last_error=None,
                        ended_at=None,
                        approval_required=True,
                        executed_task_id=(executed_task_ids[-1] if executed_task_ids else None),
                        executed_task_ids=executed_task_ids,
                    )

                write_outcome = await self._execute_serial_task(
                    run=run,
                    session=session,
                    mutable_state=mutable_state,
                    context_snapshot=context_snapshot,
                    task=phase_task,
                )
                merge_result = self._merge_execution_outcomes(
                    run=run,
                    outcomes=[write_outcome],
                    mutable_state=mutable_state,
                    tasks=tasks,
                    tool_results=tool_results,
                    reflection_summaries=reflection_summaries,
                    executed_task_ids=executed_task_ids,
                    scheduler_group=phase.scheduler_group,
                )
                latest_directive = self._directive_from_merge_result(merge_result, latest_directive)
                self._transcript_runtime.set_last_directive(mutable_state, latest_directive)
                merge_events.append(merge_result)
                merge_partial_failures = merge_result.get("partial_failures")
                if isinstance(merge_partial_failures, list):
                    partial_failures.extend(
                        [dict(item) for item in merge_partial_failures if isinstance(item, dict)]
                    )
                last_executed_task = write_outcome.task
                if write_outcome.reflection.replanning_suggestion is not None:
                    last_failure_reason = write_outcome.reflection.failure_reason
                context_snapshot = self._build_context_snapshot(
                    run=run,
                    session=session,
                    mutable_state=mutable_state,
                    tasks=tasks,
                )
                directive_result = self._stop_after_directive(
                    run=run,
                    tasks=tasks,
                    mutable_state=mutable_state,
                    loop_state=loop_state,
                    schedule=schedule,
                    context_snapshot=context_snapshot,
                    tool_results=tool_results,
                    reflection_summaries=reflection_summaries,
                    merge_events=merge_events,
                    partial_failures=partial_failures,
                    executed_task_ids=executed_task_ids,
                    latest_directive=latest_directive,
                    last_executed_task=last_executed_task,
                    last_failure_reason=last_failure_reason,
                    blocked_task_id=self._blocked_task_id_from_merge_result(merge_result),
                )
                if directive_result is not None:
                    return directive_result

        resolved_status = self._runtime.resolve_run_status(tasks)
        ended_at = datetime.now(UTC) if resolved_status is WorkflowRunStatus.DONE else None
        current_stage = (
            self._runtime.task_stage(last_executed_task)
            if last_executed_task
            else run.current_stage
        )
        mutable_state["current_stage"] = current_stage
        mutable_state["approval"] = {"required": False, "pending_task_id": None}
        self._finish_batch_cycle(mutable_state, executed_task_ids=executed_task_ids)
        last_error = last_failure_reason if resolved_status is WorkflowRunStatus.ERROR else None
        context_snapshot = self._build_context_snapshot(
            run=run,
            session=session,
            mutable_state=mutable_state,
            tasks=tasks,
        )
        loop_state = loop_state.append_cycle(
            self._build_cycle_artifact(
                mutable_state,
                schedule=schedule,
                context_snapshot=context_snapshot,
                tool_results=tool_results,
                reflection_summaries=reflection_summaries,
                merge_summary=self._build_merge_summary(
                    merge_events=merge_events,
                    executed_task_ids=executed_task_ids,
                    partial_failures=partial_failures,
                ),
                partial_failures=partial_failures,
                next_action=self._transcript_runtime.directive_to_next_action(
                    self._transcript_runtime.set_last_directive(
                        mutable_state,
                        (
                            self._transcript_runtime.directive_for_run_status(resolved_status)
                            if resolved_status is not WorkflowRunStatus.RUNNING
                            else latest_directive
                        ),
                    )
                ),
            )
        )
        loop_state.apply_to_state(mutable_state)
        executed_task_id = executed_task_ids[-1] if executed_task_ids else None
        return LoopAdvanceResult(
            status=resolved_status,
            current_stage=current_stage,
            state=mutable_state,
            last_error=last_error,
            ended_at=ended_at,
            approval_required=False,
            executed_task_id=executed_task_id,
            executed_task_ids=executed_task_ids,
        )

    @staticmethod
    def _retry_limit(task: TaskNode) -> int:
        value = task.metadata_json.get("retry_limit", 1)
        if isinstance(value, int) and value >= 0:
            return value
        return 1

    @staticmethod
    def _retry_count(task: TaskNode) -> int:
        value = task.metadata_json.get("retry_count", 0)
        if isinstance(value, int) and value >= 0:
            return value
        return 0

    def _apply_execution_result(
        self,
        *,
        run: WorkflowRun,
        task: TaskNode,
        execution: ExecutionResult,
        reflection: ReflectionResult,
        mutable_state: dict[str, object],
        tasks: list[TaskNode],
        scheduler_group: str | None,
    ) -> NextTurnDirective:
        if execution.status is TaskNodeStatus.COMPLETED and reflection.conclusion == "success":
            task.status = TaskNodeStatus.COMPLETED
        elif execution.status is TaskNodeStatus.BLOCKED:
            task.status = TaskNodeStatus.BLOCKED
        else:
            task.status = TaskNodeStatus.FAILED
        attempts = self._metadata_int(task.metadata_json, "attempt_count", default=0) + 1
        task.metadata_json = {
            **dict(task.metadata_json),
            "attempt_count": attempts,
            "last_attempt_status": task.status.value,
            "retry_scheduled": False,
            "summary": str(
                task.metadata_json.get("summary") or task.metadata_json.get("description") or ""
            ),
            "evidence_confidence": reflection.evidence_confidence,
        }
        self._runtime.sync_execution_state(task)

        cycle_id = f"cycle-{self._batch_cycle(mutable_state)}"
        transcript_append = self._transcript_runtime.append_execution(
            mutable_state=mutable_state,
            task=task,
            execution=execution,
            reflection=reflection,
            cycle_id=cycle_id,
            scheduler_group=scheduler_group,
        )

        execution_records = mutable_state.get("execution_records", [])
        if not isinstance(execution_records, list):
            execution_records = []
        execution_records.append(
            self._transcript_runtime.project_execution_record(
                session_id=run.session_id,
                task=task,
                execution=execution,
                reflection=reflection,
                batch_cycle=self._batch_cycle(mutable_state),
                retry_attempt=attempts,
                retry_count=self._metadata_int(task.metadata_json, "retry_count", default=0),
                transcript_delta_id=transcript_append.delta.delta_id,
                tool_result_record=transcript_append.tool_result_record,
            )
        )
        mutable_state["execution_records"] = execution_records
        self._trim_execution_context(mutable_state)
        self._trim_message_context(mutable_state)

        pending_protocol = self._pause_runtime.register_pending_protocol(
            mutable_state=mutable_state,
            task=task,
            execution=execution,
        )
        if isinstance(pending_protocol, dict):
            self._transcript_runtime.append_protocol_pending_event(
                mutable_state=mutable_state,
                cycle_id=cycle_id,
                current_stage=self._runtime.task_stage(task),
                task_name=task.name,
                pending_entry=pending_protocol,
            )
            if str(pending_protocol.get("kind") or "") == "approval":
                pending_resume_payload = pending_protocol.get("resume_payload")
                mutable_state["approval"] = {
                    "required": True,
                    "pending_task_id": task.id,
                    "resume_payload": (
                        dict(pending_resume_payload)
                        if isinstance(pending_resume_payload, dict)
                        else {}
                    ),
                }

        hypothesis_updates = mutable_state.get("hypothesis_updates", [])
        if not isinstance(hypothesis_updates, list):
            hypothesis_updates = []
        hypothesis_updates.extend(reflection.hypothesis_updates)
        mutable_state["hypothesis_updates"] = hypothesis_updates

        findings = mutable_state.get("findings", [])
        if not isinstance(findings, list):
            findings = []
        if reflection.finding is not None:
            findings.append(dict(reflection.finding))
        mutable_state["findings"] = findings

        graph_updates = mutable_state.get("graph_updates", [])
        if not isinstance(graph_updates, list):
            graph_updates = []
        graph_updates.append(
            {
                "trace_id": execution.trace_id,
                "task_id": task.id,
                "task_name": task.name,
                "status": task.status.value,
                "graphs": ["task", "evidence", "causal"],
            }
        )
        mutable_state["graph_updates"] = graph_updates

        resume_input = execution.input_payload.get("resume")
        if isinstance(resume_input, dict) and isinstance(resume_input.get("task_id"), str):
            self._pause_runtime.clear_resume_context(
                mutable_state,
                task_id=str(resume_input.get("task_id") or task.id),
            )

        self._runtime.materialize_ready_tasks(tasks)
        return transcript_append.directive

    async def _execute_parallel_read_wave(
        self,
        *,
        run: WorkflowRun,
        session: Session | None,
        mutable_state: dict[str, object],
        context_snapshot: ContextSnapshot,
        read_tasks: list[TaskNode],
    ) -> list[_TaskExecutionOutcome]:
        if not read_tasks:
            return []
        prepared_tasks = [
            (
                task,
                self._prepare_task_execution(
                    run=run,
                    session=session,
                    mutable_state=mutable_state,
                    context_snapshot=context_snapshot,
                    task=task,
                ),
            )
            for task in read_tasks
        ]
        outcomes = await asyncio.gather(
            *[
                self._execute_task(
                    task=task,
                    execution_context=execution_context,
                    allow_parallel=True,
                )
                for task, execution_context in prepared_tasks
            ],
            return_exceptions=True,
        )
        realized_outcomes: list[_TaskExecutionOutcome] = []
        for (task, _execution_context), outcome in zip(prepared_tasks, outcomes, strict=False):
            if isinstance(outcome, BaseException):
                realized_outcomes.append(self._build_failed_outcome(task=task, error=outcome))
                continue
            realized_outcomes.append(outcome)
        return realized_outcomes

    async def _execute_serial_task(
        self,
        *,
        run: WorkflowRun,
        session: Session | None,
        mutable_state: dict[str, object],
        context_snapshot: ContextSnapshot,
        task: TaskNode,
    ) -> _TaskExecutionOutcome:
        execution_context = self._prepare_task_execution(
            run=run,
            session=session,
            mutable_state=mutable_state,
            context_snapshot=context_snapshot,
            task=task,
        )
        return await self._execute_task(
            task=task,
            execution_context=execution_context,
            allow_parallel=False,
        )

    def _prepare_task_execution(
        self,
        *,
        run: WorkflowRun,
        session: Session | None,
        mutable_state: dict[str, object],
        context_snapshot: ContextSnapshot,
        task: TaskNode,
    ) -> WorkflowExecutionContext:
        task.status = TaskNodeStatus.IN_PROGRESS
        self._runtime.sync_execution_state(task)
        return self._build_execution_context(
            run=run,
            session=session,
            mutable_state=mutable_state,
            context_snapshot=context_snapshot,
            task=task,
        )

    async def _execute_task(
        self,
        *,
        task: TaskNode,
        execution_context: WorkflowExecutionContext,
        allow_parallel: bool,
    ) -> _TaskExecutionOutcome:
        if allow_parallel:
            execution_result = await asyncio.to_thread(
                self._executor.execute,
                context=execution_context,
                task=task,
            )
            if inspect.isawaitable(execution_result):
                execution_result = await execution_result
        else:
            execution_result = self._executor.execute(context=execution_context, task=task)
            if inspect.isawaitable(execution_result):
                execution_result = await execution_result
        reflection_result = self._reflector.review(task=task, execution=execution_result)
        if inspect.isawaitable(reflection_result):
            reflection_result = await reflection_result
        return _TaskExecutionOutcome(
            task=task,
            execution=execution_result,
            reflection=reflection_result,
            merge_candidate=self._build_concurrent_tool_payload(
                task=task,
                execution=execution_result,
                reflection=reflection_result,
            ),
        )

    def _build_failed_outcome(
        self,
        *,
        task: TaskNode,
        error: BaseException,
    ) -> _TaskExecutionOutcome:
        now = datetime.now(UTC)
        execution_result = ExecutionResult(
            trace_id=f"trace-error-{uuid4()}",
            source_type="runtime",
            source_name="workflow.executor",
            command_or_action=f"execute:{task.name}",
            input_payload={"task": task.name, "execution_error": str(error)},
            output_payload={
                "stdout": "",
                "stderr": str(error),
                "exit_code": 1,
                "raised_exception": True,
                "artifacts": [],
                "citations": [],
            },
            status=TaskNodeStatus.FAILED,
            started_at=now,
            ended_at=now,
        )
        reflection_result = self._reflector.review(task=task, execution=execution_result)
        if inspect.isawaitable(reflection_result):
            raise RuntimeError("async reflector is not supported in synchronous failure fallback")
        return _TaskExecutionOutcome(
            task=task,
            execution=execution_result,
            reflection=reflection_result,
            merge_candidate=self._build_concurrent_tool_payload(
                task=task,
                execution=execution_result,
                reflection=reflection_result,
            ),
        )

    def _build_concurrent_tool_payload(
        self,
        *,
        task: TaskNode,
        execution: ExecutionResult,
        reflection: ReflectionResult,
    ) -> _ConcurrentToolPayload:
        output_payload = (
            execution.output_payload if isinstance(execution.output_payload, dict) else {}
        )
        raw_citations = output_payload.get("citations")
        raw_artifacts = output_payload.get("artifacts")
        citations = (
            [item for item in raw_citations if isinstance(item, dict)]
            if isinstance(raw_citations, list)
            else []
        )
        artifacts = (
            [item for item in raw_artifacts if isinstance(item, dict)]
            if isinstance(raw_artifacts, list)
            else []
        )
        return _ConcurrentToolPayload(
            tool_result=self._build_tool_result(execution, task),
            context_modifier={
                "conclusion": reflection.conclusion,
                "failure_reason": reflection.failure_reason,
                "replanning_suggestion": reflection.replanning_suggestion,
                "evidence_confidence": reflection.evidence_confidence,
                "hypothesis_updates": list(reflection.hypothesis_updates),
                "finding": dict(reflection.finding) if reflection.finding is not None else None,
            },
            citations=citations,
            artifacts=artifacts,
            trace={
                "trace_id": execution.trace_id,
                "task_id": task.id,
                "task_name": task.name,
                "status": execution.status.value,
                "started_at": execution.started_at.isoformat(),
                "ended_at": execution.ended_at.isoformat(),
            },
        )

    def _build_execution_context(
        self,
        *,
        run: WorkflowRun,
        session: Session | None,
        mutable_state: dict[str, object],
        context_snapshot: ContextSnapshot,
        task: TaskNode,
    ) -> WorkflowExecutionContext:
        return WorkflowExecutionContext(
            session_id=run.session_id,
            workflow_run_id=run.id,
            project_id=session.project_id if session is not None else None,
            goal=str(mutable_state.get("goal") or "authorized assessment"),
            template_name=run.template_name,
            current_stage=self._runtime.task_stage(task),
            runtime_policy=(
                dict(policy)
                if isinstance((policy := mutable_state.get("runtime_policy")), dict)
                else {}
            ),
            retrieval=context_snapshot.retrieval.to_state(),
            memory=context_snapshot.memory.to_state(),
            context_projection=context_snapshot.projection.to_state(),
            prompting=dict(context_snapshot.prompting),
            resume=self._pause_runtime.resume_context_for_task(mutable_state, task_id=task.id),
        )

    def _merge_execution_outcomes(
        self,
        *,
        run: WorkflowRun,
        outcomes: list[_TaskExecutionOutcome],
        mutable_state: dict[str, object],
        tasks: list[TaskNode],
        tool_results: list[dict[str, object]],
        reflection_summaries: list[str],
        executed_task_ids: list[str],
        scheduler_group: str,
    ) -> dict[str, object]:
        merged_task_ids: list[str] = []
        partial_failures: list[dict[str, object]] = []
        directives: list[str] = []
        for outcome in outcomes:
            task = outcome.task
            execution_result = outcome.execution
            reflection_result = outcome.reflection
            directive = self._apply_execution_result(
                run=run,
                task=task,
                execution=execution_result,
                reflection=reflection_result,
                mutable_state=mutable_state,
                tasks=tasks,
                scheduler_group=scheduler_group,
            )
            directives.append(directive.value)
            tool_results.append(dict(outcome.merge_candidate.tool_result))
            reflection_summaries.append(
                f"{task.name}:{reflection_result.conclusion}:{reflection_result.evidence_confidence:.2f}"
            )
            if reflection_result.replanning_suggestion is not None:
                self._append_replan_record(
                    mutable_state,
                    task=task,
                    execution=execution_result,
                    reflection=reflection_result,
                )
            executed_task_ids.append(task.id)
            merged_task_ids.append(task.id)
            if execution_result.status is not TaskNodeStatus.COMPLETED:
                partial_failures.append(
                    {
                        "task_id": task.id,
                        "task_name": task.name,
                        "trace_id": execution_result.trace_id,
                        "scheduler_group": scheduler_group,
                        "reason": reflection_result.failure_reason
                        or str(execution_result.output_payload.get("stderr") or "execution_failed"),
                        "policy_denied": bool(
                            execution_result.output_payload.get("policy_denied", False)
                        ),
                    }
                )

            retry_limit = self._retry_limit(task)
            retry_count = self._retry_count(task)
            if task.status is TaskNodeStatus.FAILED and retry_count < retry_limit:
                task.metadata_json = {
                    **dict(task.metadata_json),
                    "retry_count": retry_count + 1,
                    "retry_scheduled": True,
                }
                task.status = TaskNodeStatus.READY
                self._runtime.sync_execution_state(task)
        return {
            "scheduler_group": scheduler_group,
            "merged_task_ids": merged_task_ids,
            "merged_count": len(merged_task_ids),
            "merged_after_batch_completion": True,
            "partial_failures": partial_failures,
            "directives": directives,
        }

    @staticmethod
    def _metadata_int(metadata: dict[str, object], key: str, *, default: int) -> int:
        value = metadata.get(key, default)
        if isinstance(value, int):
            return value
        return default

    @staticmethod
    def _ensure_batch_contract(mutable_state: dict[str, object]) -> None:
        batch = mutable_state.get("batch", {})
        if not isinstance(batch, dict):
            batch = {}
        mutable_state["batch"] = {
            "contract_version": "v1",
            "cycle": int(batch.get("cycle", 0)) if isinstance(batch.get("cycle", 0), int) else 0,
            "status": str(batch.get("status") or "idle"),
            "max_nodes_per_cycle": (
                int(batch.get("max_nodes_per_cycle", WorkflowGraphRuntime.DEFAULT_BATCH_SIZE))
                if isinstance(
                    batch.get("max_nodes_per_cycle", WorkflowGraphRuntime.DEFAULT_BATCH_SIZE), int
                )
                else WorkflowGraphRuntime.DEFAULT_BATCH_SIZE
            ),
            "selected_task_ids": (
                [item for item in batch.get("selected_task_ids", []) if isinstance(item, str)]
                if isinstance(batch.get("selected_task_ids", []), list)
                else []
            ),
            "executed_task_ids": (
                [item for item in batch.get("executed_task_ids", []) if isinstance(item, str)]
                if isinstance(batch.get("executed_task_ids", []), list)
                else []
            ),
            "started_at": batch.get("started_at"),
            "ended_at": batch.get("ended_at"),
        }

    def _start_batch_cycle(
        self, mutable_state: dict[str, object], *, selected_task_ids: list[str]
    ) -> None:
        self._ensure_batch_contract(mutable_state)
        batch_raw = mutable_state.get("batch")
        batch: dict[str, object] = dict(batch_raw) if isinstance(batch_raw, dict) else {}
        cycle_raw = batch.get("cycle", 0)
        cycle = cycle_raw + 1 if isinstance(cycle_raw, int) else 1
        batch.update(
            {
                "cycle": cycle,
                "status": "running",
                "selected_task_ids": selected_task_ids,
                "executed_task_ids": [],
                "started_at": datetime.now(UTC).isoformat(),
                "ended_at": None,
            }
        )
        mutable_state["batch"] = batch

    def _finish_batch_cycle(
        self, mutable_state: dict[str, object], *, executed_task_ids: list[str]
    ) -> None:
        self._ensure_batch_contract(mutable_state)
        batch_raw = mutable_state.get("batch")
        batch: dict[str, object] = dict(batch_raw) if isinstance(batch_raw, dict) else {}
        batch.update(
            {
                "status": "completed",
                "executed_task_ids": executed_task_ids,
                "ended_at": datetime.now(UTC).isoformat(),
            }
        )
        mutable_state["batch"] = batch

    def _update_batch_state(
        self,
        mutable_state: dict[str, object],
        *,
        status: str,
        selected_task_ids: list[str],
        executed_task_ids: list[str],
    ) -> None:
        self._ensure_batch_contract(mutable_state)
        batch_raw = mutable_state.get("batch")
        batch: dict[str, object] = dict(batch_raw) if isinstance(batch_raw, dict) else {}
        batch.update(
            {
                "status": status,
                "selected_task_ids": selected_task_ids,
                "executed_task_ids": executed_task_ids,
                "started_at": batch.get("started_at"),
                "ended_at": datetime.now(UTC).isoformat() if status != "running" else None,
            }
        )
        mutable_state["batch"] = batch

    @staticmethod
    def _batch_cycle(mutable_state: dict[str, object]) -> int:
        batch = mutable_state.get("batch", {})
        if isinstance(batch, dict) and isinstance(batch.get("cycle"), int):
            return int(batch["cycle"])
        return 0

    @staticmethod
    def _append_replan_record(
        mutable_state: dict[str, object],
        *,
        task: TaskNode,
        execution: ExecutionResult,
        reflection: ReflectionResult,
    ) -> None:
        records = mutable_state.get("replan_records", [])
        if not isinstance(records, list):
            records = []
        records.append(
            {
                "id": f"replan:{execution.trace_id}",
                "trace_id": execution.trace_id,
                "task_node_id": task.id,
                "task_name": task.name,
                "reason": reflection.failure_reason,
                "suggestion": reflection.replanning_suggestion,
                "recorded_at": datetime.now(UTC).isoformat(),
            }
        )
        mutable_state["replan_records"] = records

    @staticmethod
    def _last_failure_reason(outcomes: list[_TaskExecutionOutcome]) -> str | None:
        return next(
            (
                outcome.reflection.failure_reason
                for outcome in reversed(outcomes)
                if outcome.reflection.replanning_suggestion is not None
            ),
            None,
        )

    @staticmethod
    def _build_merge_summary(
        *,
        merge_events: list[dict[str, object]],
        executed_task_ids: list[str],
        partial_failures: list[dict[str, object]],
    ) -> dict[str, object]:
        return {
            "phase_count": len(merge_events),
            "merged_task_ids": list(executed_task_ids),
            "partial_failure_count": len(partial_failures),
            "phases": [dict(event) for event in merge_events],
        }

    def _trim_execution_context(self, mutable_state: dict[str, object]) -> None:
        records_raw = mutable_state.get("execution_records")
        records = records_raw if isinstance(records_raw, list) else []
        if len(records) <= self._max_active_execution_records:
            return

        trim_count = len(records) - self._max_active_execution_records
        trimmed = [item for item in records[:trim_count] if isinstance(item, dict)]
        remaining = [item for item in records[trim_count:] if isinstance(item, dict)]

        archived_raw = mutable_state.get("archived_execution_records")
        archived = archived_raw if isinstance(archived_raw, list) else []
        archived.extend(trimmed)
        mutable_state["archived_execution_records"] = archived
        mutable_state["execution_records"] = remaining

        compaction = self._ensure_compaction_state(mutable_state)
        execution_compaction = compaction["execution"]
        previous_execution_trim_count = execution_compaction.get("trim_count")
        execution_compaction["trim_count"] = (
            previous_execution_trim_count if isinstance(previous_execution_trim_count, int) else 0
        ) + trim_count
        execution_compaction["archived_count"] = len(archived)
        execution_compaction["last_trimmed_at"] = datetime.now(UTC).isoformat()

    def _trim_message_context(self, mutable_state: dict[str, object]) -> None:
        messages_raw = mutable_state.get("messages")
        messages = messages_raw if isinstance(messages_raw, list) else []
        if len(messages) <= self._max_active_messages:
            return

        trim_count = len(messages) - self._max_active_messages
        trimmed = [item for item in messages[:trim_count] if isinstance(item, dict)]
        remaining = [item for item in messages[trim_count:] if isinstance(item, dict)]

        archived_raw = mutable_state.get("archived_messages")
        archived = archived_raw if isinstance(archived_raw, list) else []
        archived.extend(trimmed)
        mutable_state["archived_messages"] = archived
        mutable_state["messages"] = remaining

        compaction = self._ensure_compaction_state(mutable_state)
        message_compaction = compaction["messages"]
        previous_message_trim_count = message_compaction.get("trim_count")
        message_compaction["trim_count"] = (
            previous_message_trim_count if isinstance(previous_message_trim_count, int) else 0
        ) + trim_count
        message_compaction["archived_count"] = len(archived)
        message_compaction["last_trimmed_at"] = datetime.now(UTC).isoformat()

    @staticmethod
    def _ensure_compaction_state(mutable_state: dict[str, object]) -> dict[str, dict[str, object]]:
        compaction_raw = mutable_state.get("compaction")
        compaction = dict(compaction_raw) if isinstance(compaction_raw, dict) else {}
        execution = dict(compaction.get("execution", {}))
        messages = dict(compaction.get("messages", {}))
        compaction["execution"] = execution
        compaction["messages"] = messages
        mutable_state["compaction"] = compaction
        return {"execution": execution, "messages": messages}

    def _build_cycle_artifact(
        self,
        mutable_state: dict[str, object],
        *,
        schedule: WorkflowToolSchedule,
        context_snapshot: ContextSnapshot,
        tool_results: list[dict[str, object]],
        reflection_summaries: list[str],
        merge_summary: dict[str, object],
        partial_failures: list[dict[str, object]],
        next_action: str,
    ) -> WorkflowCycleArtifact:
        batch_raw = mutable_state.get("batch")
        batch = (
            {str(key): value for key, value in batch_raw.items()}
            if isinstance(batch_raw, dict)
            else {}
        )
        compaction_raw = mutable_state.get("compaction")
        compaction = (
            {str(key): value for key, value in compaction_raw.items()}
            if isinstance(compaction_raw, dict)
            else {}
        )
        execution_compaction_raw = compaction.get("execution")
        message_compaction_raw = compaction.get("messages")
        execution_compaction = (
            {str(key): value for key, value in execution_compaction_raw.items()}
            if isinstance(execution_compaction_raw, dict)
            else {}
        )
        message_compaction = (
            {str(key): value for key, value in message_compaction_raw.items()}
            if isinstance(message_compaction_raw, dict)
            else {}
        )
        started_at = batch.get("started_at") if isinstance(batch.get("started_at"), str) else None
        ended_at = batch.get("ended_at") if isinstance(batch.get("ended_at"), str) else None
        cycle_id = f"cycle-{uuid4()}"
        base_cycle = WorkflowCycleArtifact(
            cycle_id=cycle_id,
            batch_cycle=self._batch_cycle(mutable_state),
            selected_tasks=[
                LoopSelectedTask(
                    task_id=task.task_id,
                    task_name=task.task_name,
                    stage_key=task.stage_key,
                    priority=task.priority,
                    approval_required=task.approval_required,
                    tool_name=task.tool_name,
                    writes_state=task.writes_state,
                    is_concurrency_safe=task.is_concurrency_safe,
                    is_read_only=task.is_read_only,
                    is_destructive=task.is_destructive,
                    scheduler_group=task.scheduler_group,
                    access_mode=task.access_mode,
                    side_effect_level=task.side_effect_level,
                    resource_keys=task.resource_keys,
                )
                for task in schedule.selected_tasks
            ],
            scheduler_mode=schedule.scheduler_mode,
            parallel_read_group=[task.task_id for task in schedule.parallel_read_group],
            serialized_write_group=[task.task_id for task in schedule.serialized_write_group],
            scheduler_summary=build_scheduler_summary(schedule),
            merge_summary=dict(merge_summary),
            partial_failures=list(partial_failures),
            retrieval_summary=context_snapshot.retrieval.summary,
            retrieval=context_snapshot.retrieval.to_state(),
            tool_results=list(tool_results),
            reflection_summary=(
                "; ".join(reflection_summaries)
                if reflection_summaries
                else "No task reflections recorded."
            ),
            memory_writes=[
                {
                    "kind": "promotions",
                    "count": len(context_snapshot.memory.promotions),
                },
                {
                    "kind": "session_distilled_entries",
                    "count": len(context_snapshot.memory.session.distilled_entries),
                },
            ],
            memory=context_snapshot.memory.to_state(),
            compaction_summary={
                "batch_status": batch.get("status"),
                "execution": execution_compaction,
                "messages": message_compaction,
                "runtime": dict(context_snapshot.compact_runtime),
                "compact_boundary_marker": context_snapshot.compact_runtime.get("boundary_marker"),
                "compact_applied": bool(context_snapshot.compact_runtime.get("compacted", False)),
                "compact_triggered": bool(context_snapshot.compact_runtime.get("triggered", False)),
                "projection_active_level": context_snapshot.projection.active_level,
                "projection_summary": context_snapshot.projection.summary,
            },
            context_projection=context_snapshot.projection.to_state(),
            next_action=next_action,
            started_at=started_at,
            ended_at=ended_at,
        )
        reflection_summary = base_cycle.reflection_summary
        bundle = self._assistant_turn_planner.build_bundle(
            mutable_state=mutable_state,
            cycle_id=cycle_id,
            context_snapshot=context_snapshot,
            schedule=schedule,
            tool_results=tool_results,
            reflection_summary=reflection_summary,
            partial_failures=partial_failures,
            next_action=next_action,
            next_directive=self._transcript_runtime.last_directive(mutable_state),
        )
        self._assistant_turn_planner.persist_bundle(mutable_state=mutable_state, bundle=bundle)
        context_raw = mutable_state.get("context")
        if isinstance(context_raw, dict):
            prompting_raw = context_raw.get("prompting")
            if isinstance(prompting_raw, dict):
                continuity_raw = prompting_raw.get("continuity")
                if isinstance(continuity_raw, dict):
                    continuity_raw["assistant_turn_carry_forward"] = (
                        bundle.turn_outcome.carry_forward_context
                    )
                    continuity_raw["assistant_turn_next_directive"] = (
                        bundle.turn_outcome.resulting_directive
                    )
                    continuity_raw["assistant_turn_next_hint"] = bundle.turn_outcome.next_turn_hint
        return self._assistant_turn_planner.apply_to_cycle(cycle=base_cycle, bundle=bundle)

    def _build_context_snapshot(
        self,
        *,
        run: WorkflowRun,
        session: Session | None,
        mutable_state: dict[str, object],
        tasks: list[TaskNode],
    ) -> ContextSnapshot:
        retrieval = self._retrieval_pipeline.build(
            run=run,
            session=session,
            state=mutable_state,
            tasks=tasks,
        )
        memory = self._memory_manager.build(
            session=session,
            state=mutable_state,
            retrieval=retrieval,
        )
        projection = self._context_projection_builder.build(
            state=mutable_state,
            retrieval=retrieval,
            memory=memory,
        )
        active_task_name = self._active_task_name(tasks)
        current_stage = str(mutable_state.get("current_stage") or run.current_stage or "") or None
        history_summary = self._history_summary(mutable_state)
        capability_fragments = self._capability_facade.build_prompt_fragments(
            session_id=run.session_id,
            role_prompt=self._active_task_metadata(tasks, "role_prompt"),
            sub_agent_role_prompt=self._active_task_metadata(tasks, "sub_agent_role_prompt"),
            task_name=active_task_name,
            task_description=self._active_task_metadata(tasks, "description"),
            projection_summary=projection.summary,
        )
        compact_runtime = self._compact_runtime.build_runtime_state(
            mutable_state=mutable_state,
            retrieval_summary=retrieval.summary,
            memory_summary=memory.summary,
            history_summary=history_summary,
            projection=projection,
            active_task_name=active_task_name,
            active_tasks=self._workspace_active_tasks(tasks, active_task_name=active_task_name),
            current_stage=current_stage,
            latest_turn_directive=self._transcript_runtime.last_directive(mutable_state).value,
            pending_protocol=self._workspace_pending_protocol(mutable_state),
            active_capability_inventory_summary=capability_fragments["inventory_summary"],
            selected_project_memory_entries=self._selected_project_memory_entries(
                retrieval=retrieval,
                mutable_state=mutable_state,
            ),
            current_retrieval_focus=self._current_retrieval_focus(retrieval, mutable_state),
            cycle_id=f"cycle-{self._batch_cycle(mutable_state)}",
        )
        reinjection = self._post_compact_reinjection.build_reinjection(
            compact_runtime=compact_runtime,
            retrieval_summary=retrieval.summary,
            session_memory_summary=memory.session.summary,
            current_stage=current_stage,
            task_name=active_task_name,
            capability_inventory_summary=capability_fragments["inventory_summary"],
            capability_schema_summary=capability_fragments["schema_summary"],
            capability_prompt_fragment=capability_fragments["prompt_fragment"],
            mutable_state=mutable_state,
            cycle_id=f"cycle-{self._batch_cycle(mutable_state)}",
        )
        transcript_continuity = self._transcript_runtime.prompt_continuity(mutable_state)
        raw_reinjection_provenance = reinjection.get("provenance")
        reinjection_provenance = (
            {str(key): value for key, value in raw_reinjection_provenance.items()}
            if isinstance(raw_reinjection_provenance, dict)
            else {}
        )
        raw_transcript_provenance = transcript_continuity.get("provenance")
        transcript_provenance = (
            {str(key): value for key, value in raw_transcript_provenance.items()}
            if isinstance(raw_transcript_provenance, dict)
            else {}
        )
        pause_state = self._pause_runtime.continuity_snapshot(mutable_state)
        active_pause = pause_state.get("active")
        latest_resolved_pause = pause_state.get("latest_resolved")
        prior_turn_outcome = self._assistant_turn_planner.latest_outcome(mutable_state)
        prompting = build_workflow_prompting_state(
            goal=str(mutable_state.get("goal") or "authorized assessment"),
            template_name=run.template_name,
            current_stage=current_stage,
            task_name=active_task_name,
            role_prompt=self._active_task_metadata(tasks, "role_prompt"),
            sub_agent_role_prompt=self._active_task_metadata(tasks, "sub_agent_role_prompt"),
            task_description=self._active_task_metadata(tasks, "description"),
            retrieval_summary=retrieval.summary,
            history_summary=history_summary,
            memory_summary=memory.summary,
            projection_summary=projection.summary,
            capability_inventory_summary=capability_fragments["inventory_summary"],
            capability_schema_summary=capability_fragments["schema_summary"],
            capability_prompt_fragment=capability_fragments["prompt_fragment"],
            compact_summary=str(transcript_continuity.get("compact_continuity") or ""),
            reinjection_summary=str(transcript_continuity.get("reinjection_continuity") or ""),
            transcript_delta_summary=str(
                transcript_continuity.get("recent_tool_result_continuity") or ""
            ),
            continuity_metadata=reinjection_provenance
            | transcript_provenance
            | {
                "compact_applied": bool(reinjection.get("compact_applied", False)),
                "assistant_turn_carry_forward": (
                    prior_turn_outcome.carry_forward_context
                    if prior_turn_outcome is not None
                    else ""
                ),
                "assistant_turn_next_directive": (
                    prior_turn_outcome.resulting_directive
                    if prior_turn_outcome is not None
                    else self._transcript_runtime.last_directive(mutable_state).value
                ),
                "assistant_turn_next_hint": (
                    prior_turn_outcome.next_turn_hint if prior_turn_outcome is not None else ""
                ),
                "pending_protocol_kind": (
                    str(active_pause.get("kind") or "") if isinstance(active_pause, dict) else ""
                ),
                "pending_protocol_pause_reason": (
                    str(active_pause.get("pause_reason") or "")
                    if isinstance(active_pause, dict)
                    else ""
                ),
                "pending_protocol_resume_condition": (
                    str(active_pause.get("resume_condition") or "")
                    if isinstance(active_pause, dict)
                    else ""
                ),
                "resolved_protocol_kind": (
                    str(latest_resolved_pause.get("kind") or "")
                    if isinstance(latest_resolved_pause, dict)
                    else ""
                ),
                "resolved_protocol_payload": (
                    dict(latest_resolved_pause.get("resolution") or {})
                    if isinstance(latest_resolved_pause, dict)
                    else {}
                ),
                "workspace_state": self._workspace_state_continuity(
                    compact_runtime=compact_runtime,
                    reinjection=reinjection,
                ),
            },
        )
        snapshot = ContextSnapshot(
            retrieval=retrieval,
            memory=memory,
            projection=projection,
            prompting=prompting,
            compact_runtime=compact_runtime,
        )
        mutable_state["context"] = snapshot.to_state()
        return snapshot

    def _return_pending_protocol_pause(
        self,
        *,
        run: WorkflowRun,
        tasks: list[TaskNode],
        mutable_state: dict[str, object],
        loop_state: WorkflowLoopState,
        context_snapshot: ContextSnapshot,
        pending_entry: dict[str, object],
    ) -> LoopAdvanceResult:
        pending_task_id = pending_entry.get("task_id")
        pending_task = next(
            (
                task
                for task in tasks
                if isinstance(pending_task_id, str) and task.id == pending_task_id
            ),
            None,
        )
        current_stage = (
            self._runtime.task_stage(pending_task)
            if pending_task is not None
            else run.current_stage
        )
        mutable_state["current_stage"] = current_stage
        is_approval = str(pending_entry.get("kind") or "") == "approval"
        self._update_batch_state(
            mutable_state,
            status="waiting_approval" if is_approval else "blocked",
            selected_task_ids=[],
            executed_task_ids=[],
        )
        idle_schedule = self._tool_scheduler.build_schedule(
            RunnableSelection(
                batch_size=self._runtime.resolve_batch_size(mutable_state),
                selected_tasks=[],
            )
        )
        directive = (
            NextTurnDirective.AWAIT_APPROVAL if is_approval else NextTurnDirective.AWAIT_USER_INPUT
        )
        loop_state = loop_state.append_cycle(
            self._build_cycle_artifact(
                mutable_state,
                schedule=idle_schedule,
                context_snapshot=context_snapshot,
                tool_results=[],
                reflection_summaries=[],
                merge_summary=self._build_merge_summary(
                    merge_events=[],
                    executed_task_ids=[],
                    partial_failures=[],
                ),
                partial_failures=[],
                next_action=self._transcript_runtime.directive_to_next_action(
                    self._transcript_runtime.set_last_directive(mutable_state, directive)
                ),
            )
        )
        loop_state.apply_to_state(mutable_state)
        return LoopAdvanceResult(
            status=(WorkflowRunStatus.NEEDS_APPROVAL if is_approval else WorkflowRunStatus.BLOCKED),
            current_stage=current_stage,
            state=mutable_state,
            last_error=None,
            ended_at=None,
            approval_required=is_approval,
            executed_task_id=None,
            executed_task_ids=[],
        )

    @staticmethod
    def _sync_compatibility_approval_state(
        *,
        mutable_state: dict[str, object],
        resolved_protocol: dict[str, object],
        resolved_task: TaskNode | None,
    ) -> None:
        if str(resolved_protocol.get("kind") or "") == "approval":
            resolved_payload = resolved_protocol.get("resolution")
            mutable_state["approval"] = {
                "required": False,
                "pending_task_id": None,
                "resolved": True,
                "resolution": (
                    dict(resolved_payload) if isinstance(resolved_payload, dict) else {}
                ),
            }
            return
        if resolved_task is not None:
            mutable_state["approval"] = {
                "required": False,
                "pending_task_id": None,
            }

    @staticmethod
    def _workspace_active_tasks(tasks: list[TaskNode], *, active_task_name: str) -> list[str]:
        active_tasks = [
            task.name
            for task in tasks
            if task.status
            in {TaskNodeStatus.IN_PROGRESS, TaskNodeStatus.READY, TaskNodeStatus.BLOCKED}
        ]
        if active_tasks:
            return active_tasks[:4]
        return [active_task_name] if active_task_name else []

    def _workspace_pending_protocol(self, mutable_state: dict[str, object]) -> dict[str, object]:
        pause_snapshot = self._pause_runtime.continuity_snapshot(mutable_state)
        active = pause_snapshot.get("active")
        if not isinstance(active, dict):
            return {}
        return {
            "kind": str(active.get("kind") or ""),
            "pause_reason": str(active.get("pause_reason") or ""),
            "resume_condition": str(active.get("resume_condition") or ""),
            "task_id": str(active.get("task_id") or ""),
            "task_name": str(active.get("task_name") or ""),
        }

    @staticmethod
    def _selected_project_memory_entries(
        *, retrieval: object, mutable_state: dict[str, object]
    ) -> list[str]:
        if not hasattr(retrieval, "project"):
            return WorkflowLoopEngine._project_manifest_source_ids(mutable_state)
        project_pack = getattr(retrieval, "project")
        items = getattr(project_pack, "items", [])
        selected: list[str] = []
        if isinstance(items, list):
            for item in items:
                metadata = getattr(item, "metadata", {})
                if isinstance(metadata, dict):
                    entry_id = metadata.get("memory_entry_id")
                    if isinstance(entry_id, str) and entry_id:
                        selected.append(entry_id)
        return selected or WorkflowLoopEngine._project_manifest_source_ids(mutable_state)

    @staticmethod
    def _current_retrieval_focus(
        retrieval: ContextSnapshot | object, mutable_state: dict[str, object]
    ) -> dict[str, object]:
        retrieval_manifest = mutable_state.get("retrieval_manifest")
        project_manifest = (
            retrieval_manifest.get("project") if isinstance(retrieval_manifest, dict) else {}
        )
        sources = project_manifest.get("sources") if isinstance(project_manifest, dict) else []
        top_source = sources[0] if isinstance(sources, list) and sources else {}
        return {
            "scope": "project" if isinstance(top_source, dict) and top_source else "session_local",
            "focus": (
                str(top_source.get("source_id") or "") if isinstance(top_source, dict) else ""
            ),
            "source_count": len(sources) if isinstance(sources, list) else 0,
        }

    @staticmethod
    def _project_manifest_source_ids(mutable_state: dict[str, object]) -> list[str]:
        retrieval_manifest = mutable_state.get("retrieval_manifest")
        project_manifest = (
            retrieval_manifest.get("project") if isinstance(retrieval_manifest, dict) else {}
        )
        sources = project_manifest.get("sources") if isinstance(project_manifest, dict) else []
        if not isinstance(sources, list):
            return []
        return [
            str(source.get("source_id") or "")
            for source in sources
            if isinstance(source, dict) and isinstance(source.get("source_id"), str)
        ]

    @staticmethod
    def _workspace_state_continuity(
        *, compact_runtime: dict[str, object], reinjection: dict[str, object]
    ) -> dict[str, object]:
        fragments = reinjection.get("fragments")
        if isinstance(fragments, dict) and isinstance(fragments.get("workspace_state"), dict):
            return dict(fragments.get("workspace_state") or {})
        retained_live_state = compact_runtime.get("retained_live_state")
        if isinstance(retained_live_state, dict) and isinstance(
            retained_live_state.get("workspace_state"), dict
        ):
            return dict(retained_live_state.get("workspace_state") or {})
        return {}

    @staticmethod
    def _history_summary(mutable_state: dict[str, object]) -> str:
        return TranscriptRuntimeService().history_summary(mutable_state)

    @staticmethod
    def _directive_from_merge_result(
        merge_result: dict[str, object], current: NextTurnDirective
    ) -> NextTurnDirective:
        directives = merge_result.get("directives")
        if isinstance(directives, list) and directives:
            parsed_directives: list[NextTurnDirective] = []
            for item in directives:
                if not isinstance(item, str):
                    continue
                try:
                    parsed_directives.append(NextTurnDirective(item))
                except ValueError:
                    continue
            if parsed_directives:
                return TranscriptRuntimeService.preferred_directive(
                    parsed_directives,
                    current=current,
                )
        return current

    def _stop_after_directive(
        self,
        *,
        run: WorkflowRun,
        tasks: list[TaskNode],
        mutable_state: dict[str, object],
        loop_state: WorkflowLoopState,
        schedule: WorkflowToolSchedule,
        context_snapshot: ContextSnapshot,
        tool_results: list[dict[str, object]],
        reflection_summaries: list[str],
        merge_events: list[dict[str, object]],
        partial_failures: list[dict[str, object]],
        executed_task_ids: list[str],
        latest_directive: NextTurnDirective,
        last_executed_task: TaskNode | None,
        last_failure_reason: str | None,
        blocked_task_id: str | None,
    ) -> LoopAdvanceResult | None:
        if not self._transcript_runtime.should_stop_current_cycle(latest_directive):
            return None

        resolved_status = self._status_for_directive(latest_directive, tasks=tasks)
        current_stage = (
            self._runtime.task_stage(last_executed_task)
            if last_executed_task is not None
            else run.current_stage
        )
        mutable_state["current_stage"] = current_stage
        approval_required = resolved_status is WorkflowRunStatus.NEEDS_APPROVAL
        mutable_state["approval"] = {
            "required": approval_required,
            "pending_task_id": blocked_task_id if approval_required else None,
        }
        self._update_batch_state(
            mutable_state,
            status=(
                "waiting_approval"
                if approval_required
                else "blocked" if resolved_status is WorkflowRunStatus.BLOCKED else "completed"
            ),
            selected_task_ids=[task.task_id for task in schedule.selected_tasks],
            executed_task_ids=executed_task_ids,
        )
        ended_at = datetime.now(UTC) if resolved_status is WorkflowRunStatus.DONE else None
        last_error = last_failure_reason if resolved_status is WorkflowRunStatus.ERROR else None
        loop_state = loop_state.append_cycle(
            self._build_cycle_artifact(
                mutable_state,
                schedule=schedule,
                context_snapshot=context_snapshot,
                tool_results=tool_results,
                reflection_summaries=reflection_summaries,
                merge_summary=self._build_merge_summary(
                    merge_events=merge_events,
                    executed_task_ids=executed_task_ids,
                    partial_failures=partial_failures,
                ),
                partial_failures=partial_failures,
                next_action=self._transcript_runtime.directive_to_next_action(
                    self._transcript_runtime.set_last_directive(mutable_state, latest_directive)
                ),
            )
        )
        loop_state.apply_to_state(mutable_state)
        return LoopAdvanceResult(
            status=resolved_status,
            current_stage=current_stage,
            state=mutable_state,
            last_error=last_error,
            ended_at=ended_at,
            approval_required=approval_required,
            executed_task_id=(executed_task_ids[-1] if executed_task_ids else None),
            executed_task_ids=executed_task_ids,
        )

    def _status_for_directive(
        self,
        directive: NextTurnDirective,
        *,
        tasks: list[TaskNode],
    ) -> WorkflowRunStatus:
        if directive is NextTurnDirective.AWAIT_APPROVAL:
            return WorkflowRunStatus.NEEDS_APPROVAL
        if directive is NextTurnDirective.AWAIT_USER_INPUT:
            return WorkflowRunStatus.BLOCKED
        if directive is NextTurnDirective.FINALIZE:
            return WorkflowRunStatus.DONE
        resolved_status = self._runtime.resolve_run_status(tasks)
        if (
            directive is NextTurnDirective.STOP_LOOP
            and resolved_status is WorkflowRunStatus.RUNNING
        ):
            return WorkflowRunStatus.BLOCKED
        return resolved_status

    @staticmethod
    def _blocked_task_id_from_merge_result(merge_result: dict[str, object]) -> str | None:
        partial_failures = merge_result.get("partial_failures")
        if not isinstance(partial_failures, list):
            return None
        for item in partial_failures:
            if not isinstance(item, dict):
                continue
            if item.get("reason") == "tool execution blocked pending user interaction":
                task_id = item.get("task_id")
                if isinstance(task_id, str):
                    return task_id
        return None

    @staticmethod
    def _active_task_name(tasks: list[TaskNode]) -> str:
        for task in tasks:
            if task.status is TaskNodeStatus.IN_PROGRESS:
                return task.name
        for task in tasks:
            if task.status is TaskNodeStatus.READY:
                return task.name
        return "workflow-context"

    @staticmethod
    def _active_task_metadata(tasks: list[TaskNode], key: str) -> str:
        for task in tasks:
            if task.status in {TaskNodeStatus.IN_PROGRESS, TaskNodeStatus.READY}:
                value = task.metadata_json.get(key)
                if isinstance(value, str):
                    return value
        return ""

    @staticmethod
    def _build_tool_result(execution: ExecutionResult, task: TaskNode) -> dict[str, object]:
        return {
            "trace_id": execution.trace_id,
            "task_id": task.id,
            "task_name": task.name,
            "command_or_action": execution.command_or_action,
            "source_name": execution.source_name,
            "status": execution.status.value,
        }
