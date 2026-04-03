from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from app.agent.context_models import ContextSnapshot
from app.agent.context_projection import ContextProjectionBuilder
from app.agent.executor import ExecutionResult, Executor
from app.agent.loop_models import LoopSelectedTask, WorkflowCycleArtifact, WorkflowLoopState
from app.agent.memory import MemoryManager
from app.agent.prompting import build_workflow_prompting_state
from app.agent.reflector import ReflectionResult, Reflector
from app.agent.retrieval import RetrievalPipeline
from app.agent.selection import RunnableSelection, WorkflowRunnableSelector
from app.agent.tool_scheduler import (
    WorkflowToolSchedule,
    WorkflowToolScheduler,
    build_scheduler_summary,
)
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
        self._context_projection_builder = ContextProjectionBuilder()
        self._runtime = runtime or WorkflowGraphRuntime()
        self._selector = selector or WorkflowRunnableSelector(
            self._runtime,
            self._executor.resolve_tool_spec,
        )
        self._tool_scheduler = WorkflowToolScheduler()

    async def advance(
        self, *, run: WorkflowRun, tasks: list[TaskNode], approve: bool
    ) -> LoopAdvanceResult:
        mutable_state = dict(run.state_json)
        self._ensure_batch_contract(mutable_state)
        loop_state = WorkflowLoopState.from_state(mutable_state)
        session = self._session_repository.get_session(run.session_id)
        self._runtime.materialize_ready_tasks(tasks)
        context_snapshot = self._build_context_snapshot(
            run=run,
            session=session,
            mutable_state=mutable_state,
            tasks=tasks,
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
                    next_action="await_approval",
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
                    next_action="complete" if resolved_status is WorkflowRunStatus.DONE else "idle",
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
                        if self._runtime.approval_required(phase_task) and not approve
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
                            next_action="await_approval",
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
                if self._runtime.approval_required(phase_task) and not approve:
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
                            next_action="await_approval",
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
                merge_events.append(merge_result)
                merge_partial_failures = merge_result.get("partial_failures")
                if isinstance(merge_partial_failures, list):
                    partial_failures.extend(
                        [dict(item) for item in merge_partial_failures if isinstance(item, dict)]
                    )
                last_executed_task = write_outcome.task
                if write_outcome.reflection.replanning_suggestion is not None:
                    last_failure_reason = write_outcome.reflection.failure_reason

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
                next_action=(
                    "complete" if resolved_status is WorkflowRunStatus.DONE else "continue"
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
    ) -> None:
        if execution.status is TaskNodeStatus.COMPLETED and reflection.conclusion == "success":
            task.status = TaskNodeStatus.COMPLETED
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

        execution_records = mutable_state.get("execution_records", [])
        if not isinstance(execution_records, list):
            execution_records = []
        execution_records.append(
            {
                "id": execution.trace_id,
                "session_id": run.session_id,
                "task_node_id": task.id,
                "source_type": execution.source_type,
                "source_name": execution.source_name,
                "command_or_action": execution.command_or_action,
                "input_json": dict(execution.input_payload),
                "output_json": dict(execution.output_payload),
                "status": task.status.value,
                "batch_cycle": self._batch_cycle(mutable_state),
                "retry_attempt": attempts,
                "retry_count": self._metadata_int(task.metadata_json, "retry_count", default=0),
                "summary": task.metadata_json.get("summary"),
                "evidence_confidence": reflection.evidence_confidence,
                "started_at": execution.started_at.isoformat(),
                "ended_at": execution.ended_at.isoformat(),
            }
        )
        mutable_state["execution_records"] = execution_records
        self._trim_execution_context(mutable_state)
        self._trim_message_context(mutable_state)

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

        self._runtime.materialize_ready_tasks(tasks)

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
        for outcome in outcomes:
            task = outcome.task
            execution_result = outcome.execution
            reflection_result = outcome.reflection
            self._apply_execution_result(
                run=run,
                task=task,
                execution=execution_result,
                reflection=reflection_result,
                mutable_state=mutable_state,
                tasks=tasks,
            )
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
        return WorkflowCycleArtifact(
            cycle_id=f"cycle-{uuid4()}",
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
                "projection_active_level": context_snapshot.projection.active_level,
                "projection_summary": context_snapshot.projection.summary,
            },
            context_projection=context_snapshot.projection.to_state(),
            next_action=next_action,
            started_at=started_at,
            ended_at=ended_at,
        )

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
        capability_fragments = self._capability_facade.build_prompt_fragments(
            session_id=run.session_id,
            role_prompt=self._active_task_metadata(tasks, "role_prompt"),
            sub_agent_role_prompt=self._active_task_metadata(tasks, "sub_agent_role_prompt"),
            task_name=active_task_name,
            task_description=self._active_task_metadata(tasks, "description"),
            projection_summary=projection.summary,
        )
        prompting = build_workflow_prompting_state(
            goal=str(mutable_state.get("goal") or "authorized assessment"),
            template_name=run.template_name,
            current_stage=str(mutable_state.get("current_stage") or "") or None,
            task_name=active_task_name,
            role_prompt=self._active_task_metadata(tasks, "role_prompt"),
            sub_agent_role_prompt=self._active_task_metadata(tasks, "sub_agent_role_prompt"),
            task_description=self._active_task_metadata(tasks, "description"),
            retrieval_summary=retrieval.summary,
            history_summary=self._history_summary(mutable_state),
            memory_summary=memory.summary,
            projection_summary=projection.summary,
            capability_inventory_summary=capability_fragments["inventory_summary"],
            capability_schema_summary=capability_fragments["schema_summary"],
            capability_prompt_fragment=capability_fragments["prompt_fragment"],
        )
        snapshot = ContextSnapshot(
            retrieval=retrieval,
            memory=memory,
            projection=projection,
            prompting=prompting,
        )
        mutable_state["context"] = snapshot.to_state()
        return snapshot

    @staticmethod
    def _history_summary(mutable_state: dict[str, object]) -> str:
        records_raw = mutable_state.get("execution_records")
        records = records_raw if isinstance(records_raw, list) else []
        if not records:
            return "No prior workflow execution records are currently active."
        lines = ["Recent workflow execution history:"]
        for item in records[-5:]:
            if not isinstance(item, dict):
                continue
            task_name = str(item.get("task_name") or item.get("task_node_id") or "unknown-task")
            status = str(item.get("status") or "unknown")
            summary = str(item.get("summary") or "")
            lines.append(f"- {task_name}: {status} {summary}".strip())
        return "\n".join(lines)

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
