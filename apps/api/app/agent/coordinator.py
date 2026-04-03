from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.agent.context_models import ContextSnapshot
from app.agent.executor import ExecutionResult, Executor
from app.agent.graph_manager import GraphManager
from app.agent.loop_engine import WorkflowLoopEngine
from app.agent.loop_models import WorkflowLoopState
from app.agent.planner import PlannedWorkflow, Planner
from app.agent.reflector import ReflectionResult, Reflector
from app.agent.transcript_runtime import TranscriptRuntimeService
from app.agent.workflow import WorkflowGraphRuntime
from app.db.models import Session, TaskNode, TaskNodeStatus, WorkflowRun, WorkflowRunStatus
from app.db.repositories import GraphRepository, RunLogRepository, SessionRepository
from app.services.capabilities import CapabilityFacade
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
    _transcript_runtime = TranscriptRuntimeService()

    def __init__(
        self,
        *,
        planner: Planner,
        executor: Executor,
        reflector: Reflector,
        graph_manager: GraphManager,
        session_repository: SessionRepository,
        run_log_repository: RunLogRepository,
        graph_repository: GraphRepository,
        capability_facade: CapabilityFacade,
    ) -> None:
        self._planner = planner
        self._executor = executor
        self._reflector = reflector
        self._graph_manager = graph_manager
        self._loop_engine = WorkflowLoopEngine(
            executor=executor,
            reflector=reflector,
            max_active_execution_records=self.MAX_ACTIVE_EXECUTION_RECORDS,
            max_active_messages=self.MAX_ACTIVE_MESSAGES,
            session_repository=session_repository,
            run_log_repository=run_log_repository,
            graph_repository=graph_repository,
            capability_facade=capability_facade,
        )

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
            "runtime_transcript": self._transcript_runtime.empty_state(),
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
            "context": ContextSnapshot.empty().to_state(),
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
        WorkflowLoopState.empty().apply_to_state(state)
        return CoordinatorStartResult(plan=plan, state=state, current_stage=current_stage)

    async def advance_workflow(
        self,
        *,
        run: WorkflowRun,
        tasks: list[TaskNode],
        approve: bool,
        user_input: str | None,
        resume_token: str | None,
        resolution_payload: dict[str, object] | None,
    ) -> CoordinatorStepResult:
        step_result = await self._loop_engine.advance(
            run=run,
            tasks=tasks,
            approve=approve,
            user_input=user_input,
            resume_token=resume_token,
            resolution_payload=resolution_payload,
        )
        executed_task_ids = list(step_result.executed_task_ids)
        active_records = step_result.state.get("execution_records")
        archived_records = step_result.state.get("archived_execution_records")
        records = [
            *(
                [item for item in archived_records if isinstance(item, dict)]
                if isinstance(archived_records, list)
                else []
            ),
            *(
                [item for item in active_records if isinstance(item, dict)]
                if isinstance(active_records, list)
                else []
            ),
        ]
        batch_state = step_result.state.get("batch")
        batch_cycle = (
            batch_state.get("cycle")
            if isinstance(batch_state, dict) and isinstance(batch_state.get("cycle"), int)
            else None
        )
        runtime_tool_records = self._transcript_runtime.recent_tool_result_records(
            step_result.state, limit=10_000
        )
        transcript_cycle_id = f"cycle-{batch_cycle}" if isinstance(batch_cycle, int) else None
        transcript_record_index = {
            record.get("task_id"): record
            for record in runtime_tool_records
            if isinstance(record.get("task_id"), str)
            and (
                transcript_cycle_id is None
                or str(record.get("cycle_id") or "") == transcript_cycle_id
            )
        }
        record_index = {
            record.get("task_node_id"): record
            for record in records
            if isinstance(record, dict)
            and isinstance(record.get("task_node_id"), str)
            and (batch_cycle is None or record.get("batch_cycle") == batch_cycle)
        }
        for task in tasks:
            if task.id not in executed_task_ids:
                continue
            transcript_record = transcript_record_index.get(task.id)
            if isinstance(transcript_record, dict):
                execution = ExecutionResult(
                    trace_id=str(transcript_record.get("trace_id") or ""),
                    source_type=str(transcript_record.get("source_type") or "runtime"),
                    source_name=str(transcript_record.get("source_name") or "workflow-engine"),
                    command_or_action=str(
                        transcript_record.get("command_or_action") or f"execute:{task.name}"
                    ),
                    input_payload=(
                        dict(payload)
                        if isinstance((payload := transcript_record.get("input_payload")), dict)
                        else {}
                    ),
                    output_payload=(
                        dict(payload)
                        if isinstance((payload := transcript_record.get("output_payload")), dict)
                        else {}
                    ),
                    status=TaskNodeStatus(
                        str(transcript_record.get("status") or task.status.value)
                    ),
                    started_at=datetime.fromisoformat(str(transcript_record.get("started_at"))),
                    ended_at=datetime.fromisoformat(str(transcript_record.get("ended_at"))),
                )
            else:
                record = record_index.get(task.id)
                if not isinstance(record, dict):
                    continue
                execution = ExecutionResult(
                    trace_id=str(record.get("id") or ""),
                    source_type=str(record.get("source_type") or "runtime"),
                    source_name=str(record.get("source_name") or "workflow-engine"),
                    command_or_action=str(
                        record.get("command_or_action") or f"execute:{task.name}"
                    ),
                    input_payload=(
                        dict(payload)
                        if isinstance((payload := record.get("input_json")), dict)
                        else {}
                    ),
                    output_payload=(
                        dict(payload)
                        if isinstance((payload := record.get("output_json")), dict)
                        else {}
                    ),
                    status=task.status,
                    started_at=datetime.fromisoformat(str(record.get("started_at"))),
                    ended_at=datetime.fromisoformat(str(record.get("ended_at"))),
                )
            reflection = self._reflector.review(task=task, execution=execution)
            self._graph_manager.record_execution(
                run=run,
                task=task,
                execution=execution,
                reflection=reflection,
            )
        self._graph_manager.sync_task_graph(run=run, tasks=tasks)
        return CoordinatorStepResult(
            status=step_result.status,
            current_stage=step_result.current_stage,
            state=step_result.state,
            last_error=step_result.last_error,
            ended_at=step_result.ended_at,
            approval_required=step_result.approval_required,
            executed_task_id=step_result.executed_task_id,
            executed_task_ids=step_result.executed_task_ids,
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
