from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from app.agent.executor import ExecutionResult, Executor
from app.agent.loop_models import LoopSelectedTask, WorkflowCycleArtifact, WorkflowLoopState
from app.agent.reflector import ReflectionResult, Reflector
from app.agent.selection import RunnableSelection, WorkflowRunnableSelector
from app.agent.workflow import WorkflowExecutionContext, WorkflowGraphRuntime
from app.db.models import TaskNode, TaskNodeStatus, WorkflowRun, WorkflowRunStatus


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


class WorkflowLoopEngine:
    def __init__(
        self,
        *,
        executor: Executor,
        reflector: Reflector,
        max_active_execution_records: int,
        max_active_messages: int,
        selector: WorkflowRunnableSelector | None = None,
        runtime: WorkflowGraphRuntime | None = None,
    ) -> None:
        self._executor = executor
        self._reflector = reflector
        self._max_active_execution_records = max_active_execution_records
        self._max_active_messages = max_active_messages
        self._runtime = runtime or WorkflowGraphRuntime()
        self._selector = selector or WorkflowRunnableSelector(self._runtime)

    def advance(
        self, *, run: WorkflowRun, tasks: list[TaskNode], approve: bool
    ) -> LoopAdvanceResult:
        mutable_state = dict(run.state_json)
        self._ensure_batch_contract(mutable_state)
        loop_state = WorkflowLoopState.from_state(mutable_state)
        self._runtime.materialize_ready_tasks(tasks)

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
            loop_state = loop_state.append_cycle(
                self._build_cycle_artifact(
                    mutable_state,
                    selection=RunnableSelection(
                        batch_size=self._runtime.resolve_batch_size(mutable_state),
                        selected_tasks=[],
                    ),
                    tool_results=[],
                    reflection_summaries=[],
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
        task_by_id = {task.id: task for task in tasks}
        runnable_tasks = [
            task_by_id[task_id] for task_id in selection.selected_task_ids if task_id in task_by_id
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
                    selection=selection,
                    tool_results=[],
                    reflection_summaries=[],
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
        executed_task_ids: list[str] = []
        tool_results: list[dict[str, object]] = []
        reflection_summaries: list[str] = []
        last_executed_task: TaskNode | None = None
        last_failure_reason: str | None = None

        for runnable_task in runnable_tasks:
            if self._runtime.approval_required(runnable_task) and not approve:
                runnable_task.status = TaskNodeStatus.BLOCKED
                self._runtime.sync_execution_state(runnable_task)
                stage_key = self._runtime.task_stage(runnable_task)
                mutable_state["approval"] = {"required": True, "pending_task_id": runnable_task.id}
                mutable_state["current_stage"] = stage_key
                self._update_batch_state(
                    mutable_state,
                    status="waiting_approval",
                    selected_task_ids=selection.selected_task_ids,
                    executed_task_ids=executed_task_ids,
                )
                loop_state = loop_state.append_cycle(
                    self._build_cycle_artifact(
                        mutable_state,
                        selection=selection,
                        tool_results=tool_results,
                        reflection_summaries=reflection_summaries,
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

            runnable_task.status = TaskNodeStatus.IN_PROGRESS
            self._runtime.sync_execution_state(runnable_task)
            execution_context = WorkflowExecutionContext(
                session_id=run.session_id,
                workflow_run_id=run.id,
                goal=str(mutable_state.get("goal") or "authorized assessment"),
                template_name=run.template_name,
                current_stage=self._runtime.task_stage(runnable_task),
                runtime_policy=(
                    dict(policy)
                    if isinstance((policy := mutable_state.get("runtime_policy")), dict)
                    else {}
                ),
            )
            execution_result = self._executor.execute(context=execution_context, task=runnable_task)
            reflection_result = self._reflector.review(
                task=runnable_task, execution=execution_result
            )
            self._apply_execution_result(
                run=run,
                task=runnable_task,
                execution=execution_result,
                reflection=reflection_result,
                mutable_state=mutable_state,
                tasks=tasks,
            )
            tool_results.append(self._build_tool_result(execution_result, runnable_task))
            reflection_summaries.append(
                f"{runnable_task.name}:{reflection_result.conclusion}:{reflection_result.evidence_confidence:.2f}"
            )
            if reflection_result.replanning_suggestion is not None:
                last_failure_reason = reflection_result.failure_reason
                self._append_replan_record(
                    mutable_state,
                    task=runnable_task,
                    execution=execution_result,
                    reflection=reflection_result,
                )
            executed_task_ids.append(runnable_task.id)
            last_executed_task = runnable_task

            retry_limit = self._retry_limit(runnable_task)
            retry_count = self._retry_count(runnable_task)
            if runnable_task.status is TaskNodeStatus.FAILED and retry_count < retry_limit:
                runnable_task.metadata_json = {
                    **dict(runnable_task.metadata_json),
                    "retry_count": retry_count + 1,
                    "retry_scheduled": True,
                }
                runnable_task.status = TaskNodeStatus.READY
                self._runtime.sync_execution_state(runnable_task)

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
        loop_state = loop_state.append_cycle(
            self._build_cycle_artifact(
                mutable_state,
                selection=selection,
                tool_results=tool_results,
                reflection_summaries=reflection_summaries,
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
        selection: RunnableSelection,
        tool_results: list[dict[str, object]],
        reflection_summaries: list[str],
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
        hypothesis_updates = mutable_state.get("hypothesis_updates")
        findings = mutable_state.get("findings")
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
                )
                for task in selection.selected_tasks
            ],
            retrieval_summary=(
                f"Selected {len(selection.selected_tasks)} runnable task(s) for batch cycle "
                f"{self._batch_cycle(mutable_state)}."
            ),
            tool_results=list(tool_results),
            reflection_summary=(
                "; ".join(reflection_summaries)
                if reflection_summaries
                else "No task reflections recorded."
            ),
            memory_writes=[
                {
                    "kind": "hypothesis_updates",
                    "count": len(hypothesis_updates) if isinstance(hypothesis_updates, list) else 0,
                },
                {
                    "kind": "findings",
                    "count": len(findings) if isinstance(findings, list) else 0,
                },
            ],
            compaction_summary={
                "batch_status": batch.get("status"),
                "execution": execution_compaction,
                "messages": message_compaction,
            },
            next_action=next_action,
            started_at=started_at,
            ended_at=ended_at,
        )

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
