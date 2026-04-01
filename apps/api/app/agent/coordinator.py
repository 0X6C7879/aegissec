from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.agent.executor import ExecutionResult, Executor
from app.agent.graph_manager import GraphManager
from app.agent.planner import PlannedWorkflow, Planner
from app.agent.reflector import ReflectionResult, Reflector
from app.agent.workflow import WorkflowExecutionContext, WorkflowGraphRuntime
from app.db.models import Session, TaskNode, TaskNodeStatus, WorkflowRun, WorkflowRunStatus
from app.workflows.template_loader import WorkflowTemplate


@dataclass(frozen=True)
class CoordinatorStartResult:
    plan: PlannedWorkflow
    state: dict[str, object]
    current_stage: str


@dataclass(frozen=True)
class CoordinatorStepResult:
    status: WorkflowRunStatus
    current_stage: str | None
    state: dict[str, object]
    last_error: str | None
    ended_at: datetime | None
    approval_required: bool
    executed_task_id: str | None
    executed_task_ids: list[str] = field(default_factory=list)


class Coordinator:
    MAX_ACTIVE_EXECUTION_RECORDS = 40
    MAX_ACTIVE_MESSAGES = 30

    def __init__(
        self,
        *,
        planner: Planner,
        executor: Executor,
        reflector: Reflector,
        graph_manager: GraphManager,
    ) -> None:
        self._planner = planner
        self._executor = executor
        self._reflector = reflector
        self._graph_manager = graph_manager

    def initialize_workflow(
        self,
        *,
        session: Session,
        template: WorkflowTemplate,
        skill_snapshot: list[dict[str, object]],
        mcp_snapshot: list[dict[str, object]],
        seed_message_id: str | None,
    ) -> CoordinatorStartResult:
        goal = (
            session.goal.strip()
            if isinstance(session.goal, str) and session.goal.strip()
            else "authorized assessment"
        )
        plan = self._planner.build_plan(goal=goal, template=template)
        current_stage = plan.stage_order[0]
        state: dict[str, object] = {
            "session_id": session.id,
            "goal": goal,
            "template": template.name,
            "plan": {
                "summary": plan.summary,
                "stage_order": list(plan.stage_order),
                "nodes": [
                    {
                        "planner_key": node.planner_key,
                        "name": node.name,
                        "node_type": node.node_type.value,
                        "title": node.title,
                        "description": node.description,
                        "stage_key": node.stage_key,
                        "role": node.role,
                        "sequence": node.sequence,
                        "depends_on": list(node.depends_on),
                        "parent_key": node.parent_key,
                        "priority": node.priority,
                        "approval_required": node.approval_required,
                        "role_prompt": node.metadata.get("role_prompt", ""),
                        "sub_agent_role_prompt": node.metadata.get("sub_agent_role_prompt", ""),
                    }
                    for node in plan.nodes
                ],
            },
            "current_stage": current_stage,
            "stage_order": list(plan.stage_order),
            "messages": [],
            "skill_snapshot": list(skill_snapshot),
            "mcp_snapshot": list(mcp_snapshot),
            "runtime_policy": (
                dict(session.runtime_policy_json)
                if isinstance(session.runtime_policy_json, dict)
                else {}
            ),
            "seed_message_id": seed_message_id,
            "findings": [],
            "execution_records": [],
            "archived_execution_records": [],
            "hypothesis_updates": [],
            "graph_updates": [],
            "archived_messages": [],
            "compaction": {
                "execution": {
                    "trim_count": 0,
                    "archived_count": 0,
                    "last_trimmed_at": None,
                },
                "messages": {
                    "trim_count": 0,
                    "archived_count": 0,
                    "last_trimmed_at": None,
                },
            },
            "approval": {"required": False, "pending_task_id": None},
            "replan_records": [],
            "batch": {
                "contract_version": "v1",
                "cycle": 0,
                "status": "idle",
                "max_nodes_per_cycle": WorkflowGraphRuntime.DEFAULT_BATCH_SIZE,
                "selected_task_ids": [],
                "executed_task_ids": [],
                "started_at": None,
                "ended_at": None,
            },
        }
        return CoordinatorStartResult(plan=plan, state=state, current_stage=current_stage)

    def advance_workflow(
        self, *, run: WorkflowRun, tasks: list[TaskNode], approve: bool
    ) -> CoordinatorStepResult:
        mutable_state = dict(run.state_json)
        self._ensure_batch_contract(mutable_state)
        runtime = WorkflowGraphRuntime()
        runtime.materialize_ready_tasks(tasks)

        blocked_approval_tasks = runtime.blocked_for_approval(tasks)
        if blocked_approval_tasks and not approve:
            pending_task = blocked_approval_tasks[0]
            current_stage = runtime.task_stage(pending_task)
            mutable_state["approval"] = {"required": True, "pending_task_id": pending_task.id}
            mutable_state["current_stage"] = current_stage
            self._update_batch_state(
                mutable_state,
                status="waiting_approval",
                selected_task_ids=[],
                executed_task_ids=[],
            )
            return CoordinatorStepResult(
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
            runtime.sync_execution_state(blocked_approval_tasks[0])

        batch_size = runtime.resolve_batch_size(mutable_state)
        runnable_tasks = runtime.pick_runnable_batch(tasks, limit=batch_size)
        if not runnable_tasks:
            resolved_status = runtime.resolve_run_status(tasks)
            ended_at = datetime.now(UTC) if resolved_status is WorkflowRunStatus.DONE else None
            mutable_state["approval"] = {"required": False, "pending_task_id": None}
            self._update_batch_state(
                mutable_state,
                status="idle",
                selected_task_ids=[],
                executed_task_ids=[],
            )
            return CoordinatorStepResult(
                status=resolved_status,
                current_stage=run.current_stage,
                state=mutable_state,
                last_error=run.last_error,
                ended_at=ended_at,
                approval_required=False,
                executed_task_id=None,
                executed_task_ids=[],
            )

        self._start_batch_cycle(
            mutable_state, selected_task_ids=[task.id for task in runnable_tasks]
        )
        executed_task_ids: list[str] = []
        last_executed_task: TaskNode | None = None
        last_failure_reason: str | None = None
        selected_task_ids = [task.id for task in runnable_tasks]
        for runnable_task in runnable_tasks:
            if runtime.approval_required(runnable_task) and not approve:
                runnable_task.status = TaskNodeStatus.BLOCKED
                runtime.sync_execution_state(runnable_task)
                stage_key = runtime.task_stage(runnable_task)
                mutable_state["approval"] = {"required": True, "pending_task_id": runnable_task.id}
                mutable_state["current_stage"] = stage_key
                self._update_batch_state(
                    mutable_state,
                    status="waiting_approval",
                    selected_task_ids=selected_task_ids,
                    executed_task_ids=executed_task_ids,
                )
                self._graph_manager.sync_task_graph(run=run, tasks=tasks)
                return CoordinatorStepResult(
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
            runtime.sync_execution_state(runnable_task)
            execution_context = WorkflowExecutionContext(
                session_id=run.session_id,
                workflow_run_id=run.id,
                goal=str(mutable_state.get("goal") or "authorized assessment"),
                template_name=run.template_name,
                current_stage=runtime.task_stage(runnable_task),
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
            if reflection_result.replanning_suggestion is not None:
                last_failure_reason = reflection_result.failure_reason
                self._append_replan_record(
                    mutable_state,
                    task=runnable_task,
                    execution=execution_result,
                    reflection=reflection_result,
                )
            self._graph_manager.record_execution(
                run=run,
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
                runtime.sync_execution_state(runnable_task)

        self._graph_manager.sync_task_graph(run=run, tasks=tasks)

        resolved_status = runtime.resolve_run_status(tasks)
        ended_at = datetime.now(UTC) if resolved_status is WorkflowRunStatus.DONE else None
        current_stage = (
            runtime.task_stage(last_executed_task) if last_executed_task else run.current_stage
        )
        mutable_state["current_stage"] = current_stage
        mutable_state["approval"] = {"required": False, "pending_task_id": None}
        self._finish_batch_cycle(mutable_state, executed_task_ids=executed_task_ids)
        last_error = last_failure_reason if resolved_status is WorkflowRunStatus.ERROR else None
        executed_task_id = executed_task_ids[-1] if executed_task_ids else None
        return CoordinatorStepResult(
            status=resolved_status,
            current_stage=current_stage,
            state=mutable_state,
            last_error=last_error,
            ended_at=ended_at,
            approval_required=False,
            executed_task_id=executed_task_id,
            executed_task_ids=executed_task_ids,
        )

    def sync_graph_snapshots(self, *, run: WorkflowRun, tasks: list[TaskNode]) -> None:
        self._graph_manager.sync_task_graph(run=run, tasks=tasks)

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
        WorkflowGraphRuntime.sync_execution_state(task)

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

        WorkflowGraphRuntime().materialize_ready_tasks(tasks)

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
    def _metadata_int(metadata: dict[str, object], key: str, *, default: int) -> int:
        value = metadata.get(key, default)
        if isinstance(value, int):
            return value
        return default

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

    @classmethod
    def _trim_execution_context(cls, mutable_state: dict[str, object]) -> None:
        records_raw = mutable_state.get("execution_records")
        records = records_raw if isinstance(records_raw, list) else []
        if len(records) <= cls.MAX_ACTIVE_EXECUTION_RECORDS:
            return

        trim_count = len(records) - cls.MAX_ACTIVE_EXECUTION_RECORDS
        trimmed = [item for item in records[:trim_count] if isinstance(item, dict)]
        remaining = [item for item in records[trim_count:] if isinstance(item, dict)]

        archived_raw = mutable_state.get("archived_execution_records")
        archived = archived_raw if isinstance(archived_raw, list) else []
        archived.extend(trimmed)
        mutable_state["archived_execution_records"] = archived
        mutable_state["execution_records"] = remaining

        compaction = cls._ensure_compaction_state(mutable_state)
        execution_compaction = compaction["execution"]
        previous_execution_trim_count = execution_compaction.get("trim_count")
        execution_compaction["trim_count"] = (
            previous_execution_trim_count if isinstance(previous_execution_trim_count, int) else 0
        ) + trim_count
        execution_compaction["trim_count"] = (
            execution_compaction["trim_count"]
            if isinstance(execution_compaction["trim_count"], int)
            else trim_count
        )
        execution_compaction["archived_count"] = len(archived)
        execution_compaction["last_trimmed_at"] = datetime.now(UTC).isoformat()

    @classmethod
    def _trim_message_context(cls, mutable_state: dict[str, object]) -> None:
        messages_raw = mutable_state.get("messages")
        messages = messages_raw if isinstance(messages_raw, list) else []
        if len(messages) <= cls.MAX_ACTIVE_MESSAGES:
            return

        trim_count = len(messages) - cls.MAX_ACTIVE_MESSAGES
        trimmed = [item for item in messages[:trim_count] if isinstance(item, dict)]
        remaining = [item for item in messages[trim_count:] if isinstance(item, dict)]

        archived_raw = mutable_state.get("archived_messages")
        archived = archived_raw if isinstance(archived_raw, list) else []
        archived.extend(trimmed)
        mutable_state["archived_messages"] = archived
        mutable_state["messages"] = remaining

        compaction = cls._ensure_compaction_state(mutable_state)
        message_compaction = compaction["messages"]
        previous_message_trim_count = message_compaction.get("trim_count")
        message_compaction["trim_count"] = (
            previous_message_trim_count if isinstance(previous_message_trim_count, int) else 0
        ) + trim_count
        message_compaction["trim_count"] = (
            message_compaction["trim_count"]
            if isinstance(message_compaction["trim_count"], int)
            else trim_count
        )
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
        return {
            "execution": execution,
            "messages": messages,
        }
