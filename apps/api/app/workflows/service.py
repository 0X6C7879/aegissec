from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from fastapi import Depends
from sqlmodel import Session as DBSession

from app.agent.coordinator import Coordinator
from app.agent.executor import Executor
from app.agent.graph_manager import GraphManager
from app.agent.planner import Planner
from app.agent.reflector import Reflector
from app.agent.workflow import PlannedTaskNode, WorkflowGraphRuntime
from app.compat.mcp.client_manager import MCPClientManager
from app.compat.mcp.service import MCPService
from app.compat.skills.service import SkillService
from app.core.settings import Settings, get_settings
from app.db.models import (
    GraphType,
    RuntimeExecuteRequest,
    RuntimeExecutionRunRead,
    RuntimePolicy,
    SessionGraphRead,
    SessionStatus,
    TaskNode,
    TaskNodeStatus,
    WorkflowRun,
    WorkflowRunDetailRead,
    WorkflowRunExportRead,
    WorkflowRunReplayRead,
    WorkflowRunReplayStepRead,
    WorkflowRunStatus,
    WorkflowTemplateRead,
    WorkflowTemplateStageRead,
    to_workflow_run_detail_read,
)
from app.db.repositories import (
    GraphRepository,
    RunLogRepository,
    RuntimeRepository,
    SessionRepository,
    WorkflowRepository,
)
from app.db.session import get_db_session
from app.graphs.builders import (
    AttackGraphBuilder,
    CausalGraphBuilder,
    SnapshotGraphBuilder,
    TaskGraphBuilder,
)
from app.services.capabilities import CapabilityFacade
from app.services.runtime import RuntimeBackend, get_runtime_backend
from app.services.workflow_queue import (
    InProcessWorkflowQueueBackend,
    RedisWorkflowQueueBackend,
    WorkflowQueueBackend,
    get_workflow_queue_backend,
)
from app.workflows.template_loader import WorkflowTemplateLoader


class SessionNotFoundError(Exception):
    pass


class WorkflowTemplateNotFoundError(Exception):
    pass


class WorkflowRunNotFoundError(Exception):
    pass


class WorkflowTaskReorderValidationError(Exception):
    pass


class WorkflowApprovalRequiredError(Exception):
    def __init__(self, workflow: WorkflowRunDetailRead) -> None:
        super().__init__("Approval required.")
        self.workflow = workflow


class WorkflowService:
    def __init__(
        self,
        db_session: DBSession,
        *,
        settings: Settings,
        template_loader: WorkflowTemplateLoader | None = None,
        queue_backend: WorkflowQueueBackend | None = None,
    ) -> None:
        self._db_session = db_session
        self._settings = settings
        self._session_repository = SessionRepository(db_session)
        self._workflow_repository = WorkflowRepository(db_session)
        self._graph_repository = GraphRepository(db_session)
        self._run_log_repository = RunLogRepository(db_session)
        self._runtime_repository = RuntimeRepository(db_session)
        self._skill_service = SkillService(db_session, settings)
        self._mcp_service = MCPService(db_session, settings, MCPClientManager())
        self._queue_backend = queue_backend or get_workflow_queue_backend(
            settings,
            self._runtime_repository,
            self._run_log_repository,
        )
        self._capability_facade = CapabilityFacade(
            skill_service=self._skill_service,
            mcp_service=self._mcp_service,
            runtime_runner=self._run_runtime_command,
            run_log_repository=self._run_log_repository,
        )
        self._template_loader = template_loader or WorkflowTemplateLoader()
        self._task_graph_builder = TaskGraphBuilder()
        self._causal_graph_builder = CausalGraphBuilder()
        self._attack_graph_builder = AttackGraphBuilder()
        self._snapshot_graph_builder = SnapshotGraphBuilder()
        self._coordinator = Coordinator(
            planner=Planner(),
            executor=Executor(capability_facade=self._capability_facade),
            reflector=Reflector(),
            graph_manager=GraphManager(self._graph_repository),
            session_repository=self._session_repository,
            run_log_repository=self._run_log_repository,
            graph_repository=self._graph_repository,
            capability_facade=self._capability_facade,
        )

    def _run_runtime_command(
        self,
        payload: RuntimeExecuteRequest,
        runtime_policy: RuntimePolicy | None = None,
    ) -> RuntimeExecutionRunRead:
        return self._queue_backend.execute(payload, runtime_policy=runtime_policy)

    def start_workflow(
        self,
        *,
        session_id: str,
        template_name: str,
        seed_message_id: str | None,
    ) -> WorkflowRunDetailRead:
        session = self._session_repository.get_session(session_id)
        if session is None:
            raise SessionNotFoundError

        template = self._template_loader.load(template_name)
        if template is None:
            raise WorkflowTemplateNotFoundError

        capability_snapshot = self._capability_facade.build_snapshot(session_id=session.id)
        start_result = self._coordinator.initialize_workflow(
            session=session,
            template=template,
            skill_snapshot=cast(list[dict[str, object]], capability_snapshot["skills"]),
            mcp_snapshot=cast(list[dict[str, object]], capability_snapshot["mcp_servers"]),
            seed_message_id=seed_message_id,
        )
        started_at = datetime.now(UTC)
        run = self._workflow_repository.create_run(
            session_id=session_id,
            template_name=template.name,
            status=WorkflowRunStatus.RUNNING,
            current_stage=start_result.current_stage,
            started_at=started_at,
            ended_at=None,
            state=dict(start_result.state),
            last_error=None,
        )
        tasks = self._create_planned_tasks(run.id, start_result.plan.nodes)
        self._coordinator.sync_graph_snapshots(run=run, tasks=tasks)
        self._session_repository.update_session(
            session,
            status=SessionStatus.RUNNING,
            current_phase=run.current_stage,
        )
        return to_workflow_run_detail_read(run, tasks)

    def list_workflow_templates(self) -> list[WorkflowTemplateRead]:
        templates = self._template_loader.list_templates()
        return [
            WorkflowTemplateRead(
                name=template.name,
                title=template.title,
                description=template.description,
                template_kinds=list(template.template_kinds),
                stages=[
                    WorkflowTemplateStageRead(
                        key=stage.key,
                        title=stage.title,
                        role=stage.role,
                        phase=stage.phase,
                        role_prompt=stage.role_prompt,
                        sub_agent_role_prompt=stage.sub_agent_role_prompt,
                        requires_approval=stage.requires_approval,
                    )
                    for stage in template.stages
                ],
            )
            for template in templates
        ]

    def get_workflow(self, run_id: str) -> WorkflowRunDetailRead:
        run = self._workflow_repository.get_run(run_id)
        if run is None:
            raise WorkflowRunNotFoundError
        tasks = self._workflow_repository.list_task_nodes(run.id)
        return to_workflow_run_detail_read(run, tasks)

    async def advance_workflow(
        self,
        run_id: str,
        *,
        approve: bool,
        user_input: str | None,
        resume_token: str | None,
        resolution_payload: dict[str, object] | None,
    ) -> WorkflowRunDetailRead:
        run = self._workflow_repository.get_run(run_id)
        if run is None:
            raise WorkflowRunNotFoundError

        tasks = self._workflow_repository.list_task_nodes(run.id)

        step_result = await self._coordinator.advance_workflow(
            run=run,
            tasks=tasks,
            approve=approve,
            user_input=user_input,
            resume_token=resume_token,
            resolution_payload=resolution_payload,
        )
        for task in tasks:
            self._workflow_repository.patch_task_node(
                task,
                status=task.status,
                metadata=dict(task.metadata_json),
            )

        updated_run = self._workflow_repository.update_run(
            run,
            status=step_result.status,
            current_stage=step_result.current_stage,
            state=step_result.state,
            last_error=step_result.last_error,
            ended_at=step_result.ended_at,
        )
        self._coordinator.sync_graph_snapshots(run=updated_run, tasks=tasks)
        self._sync_session_phase(updated_run)

        if step_result.executed_task_ids or step_result.executed_task_id is not None:
            executed_task_ids = (
                list(step_result.executed_task_ids)
                if step_result.executed_task_ids
                else ([step_result.executed_task_id] if step_result.executed_task_id else [])
            )
            self._log_execution_records(updated_run, executed_task_ids=executed_task_ids)

        refreshed_tasks = self._workflow_repository.list_task_nodes(updated_run.id)
        detail = to_workflow_run_detail_read(updated_run, refreshed_tasks)
        if step_result.approval_required and not approve:
            raise WorkflowApprovalRequiredError(detail)
        return detail

    def reorder_sibling_task_priorities(
        self, run_id: str, *, ordered_task_ids: list[str]
    ) -> WorkflowRunDetailRead:
        run = self._workflow_repository.get_run(run_id)
        if run is None:
            raise WorkflowRunNotFoundError
        if not ordered_task_ids:
            raise WorkflowTaskReorderValidationError

        tasks = self._workflow_repository.list_task_nodes(run.id)
        task_by_id = {task.id: task for task in tasks}
        if len(set(ordered_task_ids)) != len(ordered_task_ids):
            raise WorkflowTaskReorderValidationError
        if any(task_id not in task_by_id for task_id in ordered_task_ids):
            raise WorkflowTaskReorderValidationError

        selected = [task_by_id[task_id] for task_id in ordered_task_ids]
        first_parent_id = selected[0].parent_id
        if any(task.parent_id != first_parent_id for task in selected):
            raise WorkflowTaskReorderValidationError

        siblings = [task for task in tasks if task.parent_id == first_parent_id]
        sibling_ids = {task.id for task in siblings}
        if set(ordered_task_ids) != sibling_ids:
            raise WorkflowTaskReorderValidationError

        total = len(ordered_task_ids)
        for index, task_id in enumerate(ordered_task_ids):
            task = task_by_id[task_id]
            metadata = {
                **dict(task.metadata_json),
                "priority": total - index,
                "sibling_priority_rank": index,
            }
            self._workflow_repository.patch_task_node(task, metadata=metadata)

        refreshed_tasks = self._workflow_repository.list_task_nodes(run.id)
        self._coordinator.sync_graph_snapshots(run=run, tasks=refreshed_tasks)
        return to_workflow_run_detail_read(run, refreshed_tasks)

    def export_workflow_run(self, run_id: str) -> WorkflowRunExportRead:
        run = self._workflow_repository.get_run(run_id)
        if run is None:
            raise WorkflowRunNotFoundError
        tasks = self._workflow_repository.list_task_nodes(run.id)
        detail = to_workflow_run_detail_read(run, tasks)
        return WorkflowRunExportRead(
            run=detail,
            task_graph=self._build_graph_snapshot(run=run, graph_type=GraphType.TASK, tasks=tasks),
            evidence_graph=self._build_graph_snapshot(
                run=run,
                graph_type=GraphType.EVIDENCE,
                tasks=tasks,
            ),
            causal_graph=self._build_graph_snapshot(
                run=run, graph_type=GraphType.CAUSAL, tasks=tasks
            ),
            attack_graph=self._build_graph_snapshot(
                run=run, graph_type=GraphType.ATTACK, tasks=tasks
            ),
            execution_records=self._execution_records_with_archived(run.state_json),
            replan_records=self._extract_dict_list(run.state_json.get("replan_records")),
            batch_state=(
                dict(batch) if isinstance((batch := run.state_json.get("batch")), dict) else {}
            ),
        )

    def replay_workflow_run(self, run_id: str) -> WorkflowRunReplayRead:
        run = self._workflow_repository.get_run(run_id)
        if run is None:
            raise WorkflowRunNotFoundError
        tasks = {task.id: task for task in self._workflow_repository.list_task_nodes(run.id)}
        execution_records = self._execution_records_with_archived(run.state_json)
        steps: list[WorkflowRunReplayStepRead] = []
        for index, record in enumerate(execution_records):
            task_node_id = record.get("task_node_id")
            task_node_name = record.get("command_or_action")
            if isinstance(task_node_id, str) and task_node_id in tasks:
                task_node_name = tasks[task_node_id].name
            steps.append(
                WorkflowRunReplayStepRead(
                    index=index,
                    trace_id=str(record.get("id") or ""),
                    task_node_id=str(task_node_id or ""),
                    task_name=str(task_node_name or "workflow-step"),
                    status=str(record.get("status") or "unknown"),
                    started_at=str(record.get("started_at") or ""),
                    ended_at=str(record.get("ended_at") or ""),
                    summary=(
                        str(summary)
                        if isinstance((summary := record.get("summary")), str)
                        else None
                    ),
                    evidence_confidence=(
                        float(confidence)
                        if isinstance(
                            (confidence := record.get("evidence_confidence")), int | float
                        )
                        else None
                    ),
                    retry_attempt=(
                        int(attempt)
                        if isinstance((attempt := record.get("retry_attempt")), int)
                        else None
                    ),
                    batch_cycle=(
                        int(cycle)
                        if isinstance((cycle := record.get("batch_cycle")), int)
                        else None
                    ),
                )
            )
        return WorkflowRunReplayRead(
            run_id=run.id,
            session_id=run.session_id,
            template_name=run.template_name,
            status=run.status,
            current_stage=run.current_stage,
            replay_steps=steps,
            replan_records=self._extract_dict_list(run.state_json.get("replan_records")),
            batch_state=(
                dict(batch) if isinstance((batch := run.state_json.get("batch")), dict) else {}
            ),
        )

    def _create_planned_tasks(
        self, workflow_run_id: str, nodes: list[PlannedTaskNode]
    ) -> list[TaskNode]:
        task_nodes: list[TaskNode] = []
        planner_key_to_task: dict[str, TaskNode] = {}
        for node in nodes:
            metadata = {
                **dict(node.metadata),
                "planner_key": node.planner_key,
                "title": node.title,
                "description": node.description,
                "summary": node.description,
                "role": node.role,
                "stage_key": node.stage_key,
                "depends_on_planner_keys": list(node.depends_on),
                "depends_on_task_ids": [],
                "priority": node.priority,
                "approval_required": node.approval_required,
                "execution_state": "pending",
                "attempt_count": 0,
                "retry_count": 0,
                "retry_limit": 1,
                "retry_scheduled": False,
                "evidence_confidence": None,
            }
            created = self._workflow_repository.create_task_node(
                workflow_run_id=workflow_run_id,
                name=node.name,
                node_type=node.node_type,
                status=TaskNodeStatus.PENDING,
                sequence=node.sequence,
                parent_id=None,
                metadata=metadata,
            )
            task_nodes.append(created)
            planner_key_to_task[node.planner_key] = created

        for node in nodes:
            task = planner_key_to_task[node.planner_key]
            depends_on_task_ids = [
                planner_key_to_task[dependency].id
                for dependency in node.depends_on
                if dependency in planner_key_to_task
            ]
            metadata = {
                **dict(task.metadata_json),
                "depends_on_task_ids": depends_on_task_ids,
            }
            status = (
                TaskNodeStatus.BLOCKED
                if node.approval_required and not depends_on_task_ids
                else (TaskNodeStatus.READY if not depends_on_task_ids else TaskNodeStatus.PENDING)
            )
            self._workflow_repository.patch_task_node(
                task,
                status=status,
                parent_id=planner_key_to_task[node.parent_key].id if node.parent_key else None,
                update_parent=True,
                metadata=metadata,
            )
            WorkflowGraphRuntime.sync_execution_state(task)
            self._workflow_repository.patch_task_node(task, metadata=dict(task.metadata_json))
        for task in task_nodes:
            if task.status is TaskNodeStatus.BLOCKED and not bool(
                task.metadata_json.get("approval_required", False)
            ):
                self._workflow_repository.patch_task_node(task, status=TaskNodeStatus.PENDING)
        return self._workflow_repository.list_task_nodes(workflow_run_id)

    def _sync_session_phase(self, run: WorkflowRun) -> None:
        session = self._session_repository.get_session(run.session_id)
        if session is None:
            return
        session_status = SessionStatus.RUNNING
        if run.status is WorkflowRunStatus.DONE:
            session_status = SessionStatus.DONE
        elif run.status is WorkflowRunStatus.ERROR:
            session_status = SessionStatus.ERROR
        elif run.status is WorkflowRunStatus.NEEDS_APPROVAL:
            session_status = SessionStatus.PAUSED
        self._session_repository.update_session(
            session,
            status=session_status,
            current_phase=run.current_stage,
        )

    def _log_execution_records(self, run: WorkflowRun, *, executed_task_ids: list[str]) -> None:
        if not executed_task_ids:
            return

        records = self._execution_records_with_archived(run.state_json)
        if not records:
            return

        record_matches = self._match_execution_records(
            run.state_json,
            records=records,
            executed_task_ids=executed_task_ids,
        )
        for record in record_matches:
            task_node_id = record.get("task_node_id")
            task_node_name = record.get("command_or_action")
            trace_id = record.get("id")
            if not isinstance(task_node_name, str):
                task_node_name = "workflow-step"
            payload = {
                "trace_id": trace_id,
                "task_node_id": task_node_id,
                "source_type": record.get("source_type"),
                "source_name": record.get("source_name"),
                "status": record.get("status"),
                "output_json": record.get("output_json", {}),
            }
            self._run_log_repository.create_log(
                session_id=run.session_id,
                run_id=None,
                level="info",
                source="workflow.executor",
                event_type="workflow.execution.recorded",
                message=f"Recorded execution result for {task_node_name}",
                payload=payload,
            )

    def _build_skill_snapshot(self) -> list[dict[str, object]]:
        return self._capability_facade.build_skill_snapshot()

    def _build_mcp_snapshot(self) -> list[dict[str, object]]:
        return self._capability_facade.build_mcp_snapshot()

    def _build_graph_snapshot(
        self,
        *,
        run: WorkflowRun,
        graph_type: GraphType,
        tasks: list[TaskNode],
    ) -> SessionGraphRead:
        graph_nodes = self._graph_repository.list_nodes(
            run.session_id,
            workflow_run_id=run.id,
            graph_type=graph_type,
        )
        graph_edges = self._graph_repository.list_edges(
            run.session_id,
            workflow_run_id=run.id,
            graph_type=graph_type,
        )
        if graph_nodes or graph_edges:
            return self._snapshot_graph_builder.build(
                session_id=run.session_id,
                workflow_run_id=run.id,
                graph_type=graph_type,
                current_stage=run.current_stage,
                nodes=graph_nodes,
                edges=graph_edges,
            )
        if graph_type is GraphType.TASK:
            return self._task_graph_builder.build(run=run, tasks=tasks)
        if graph_type is GraphType.EVIDENCE:
            return SessionGraphRead(
                session_id=run.session_id,
                workflow_run_id=run.id,
                graph_type=GraphType.EVIDENCE,
                current_stage=run.current_stage,
                nodes=[],
                edges=[],
            )
        if graph_type is GraphType.CAUSAL:
            return self._causal_graph_builder.build(run=run)
        evidence_nodes = self._graph_repository.list_nodes(
            run.session_id,
            workflow_run_id=run.id,
            graph_type=GraphType.EVIDENCE,
        )
        evidence_edges = self._graph_repository.list_edges(
            run.session_id,
            workflow_run_id=run.id,
            graph_type=GraphType.EVIDENCE,
        )
        causal_nodes = self._graph_repository.list_nodes(
            run.session_id,
            workflow_run_id=run.id,
            graph_type=GraphType.CAUSAL,
        )
        causal_edges = self._graph_repository.list_edges(
            run.session_id,
            workflow_run_id=run.id,
            graph_type=GraphType.CAUSAL,
        )
        return self._attack_graph_builder.build(
            run=run,
            tasks=tasks,
            evidence_nodes=evidence_nodes,
            evidence_edges=evidence_edges,
            causal_nodes=causal_nodes,
            causal_edges=causal_edges,
        )

    @staticmethod
    def _extract_dict_list(raw: object) -> list[dict[str, object]]:
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    @classmethod
    def _execution_records_with_archived(cls, state: dict[str, object]) -> list[dict[str, object]]:
        archived = cls._extract_dict_list(state.get("archived_execution_records"))
        current = cls._extract_dict_list(state.get("execution_records"))
        return [*archived, *current]

    @staticmethod
    def _match_execution_records(
        state: dict[str, object],
        *,
        records: list[dict[str, object]],
        executed_task_ids: list[str],
    ) -> list[dict[str, object]]:
        executed_task_id_set = set(executed_task_ids)
        batch_raw = state.get("batch")
        batch = dict(batch_raw) if isinstance(batch_raw, dict) else {}
        batch_cycle = batch.get("cycle") if isinstance(batch.get("cycle"), int) else None

        matched_records = [
            record
            for record in records
            if isinstance(record.get("task_node_id"), str)
            and record.get("task_node_id") in executed_task_id_set
            and (batch_cycle is None or record.get("batch_cycle") == batch_cycle)
        ]
        if matched_records:
            return matched_records

        fallback_records = [
            record
            for record in records
            if isinstance(record.get("task_node_id"), str)
            and record.get("task_node_id") in executed_task_id_set
        ]
        if fallback_records:
            return fallback_records[-len(executed_task_ids) :]

        return records[-len(executed_task_ids) :]


def get_workflow_service(
    db_session: DBSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    runtime_backend: RuntimeBackend = Depends(get_runtime_backend),
) -> WorkflowService:
    runtime_repository = RuntimeRepository(db_session)
    run_log_repository = RunLogRepository(db_session)
    queue_backend: WorkflowQueueBackend
    if settings.queue_backend == "redis":
        queue_backend = RedisWorkflowQueueBackend(
            settings=settings,
            runtime_repository=runtime_repository,
            run_log_repository=run_log_repository,
            runtime_backend=runtime_backend,
        )
    else:
        queue_backend = InProcessWorkflowQueueBackend(
            settings=settings,
            runtime_repository=runtime_repository,
            run_log_repository=run_log_repository,
            runtime_backend=runtime_backend,
        )
    return WorkflowService(db_session, settings=settings, queue_backend=queue_backend)
