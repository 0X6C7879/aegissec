from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Depends
from sqlmodel import Session as DBSession

from app.core.settings import Settings, get_settings
from app.db.models import (
    TaskNode,
    TaskNodeStatus,
    TaskNodeType,
    WorkflowRunDetailRead,
    WorkflowRunStatus,
    to_workflow_run_detail_read,
)
from app.db.repositories import (
    MCPRepository,
    SessionRepository,
    SkillRepository,
    WorkflowRepository,
)
from app.db.session import get_db_session
from app.workflows.engine import DeterministicWorkflowEngine
from app.workflows.template_loader import WorkflowTemplate, WorkflowTemplateLoader


class SessionNotFoundError(Exception):
    pass


class WorkflowTemplateNotFoundError(Exception):
    pass


class WorkflowRunNotFoundError(Exception):
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
        engine: DeterministicWorkflowEngine | None = None,
    ) -> None:
        del settings
        self._session_repository = SessionRepository(db_session)
        self._workflow_repository = WorkflowRepository(db_session)
        self._skill_repository = SkillRepository(db_session)
        self._mcp_repository = MCPRepository(db_session)
        self._template_loader = template_loader or WorkflowTemplateLoader()
        self._engine = engine or DeterministicWorkflowEngine()

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

        self._engine.build_graph(template)
        state = self._engine.build_initial_state(
            session_id=session_id,
            template=template,
            skill_snapshot=self._build_skill_snapshot(),
            mcp_snapshot=self._build_mcp_snapshot(),
            seed_message_id=seed_message_id,
        )
        started_at = datetime.now(UTC)
        run = self._workflow_repository.create_run(
            session_id=session_id,
            template_name=template.name,
            status=WorkflowRunStatus.RUNNING,
            current_stage=state["current_stage"],
            started_at=started_at,
            ended_at=None,
            state=dict(state),
            last_error=None,
        )
        tasks = self._create_stage_tasks(run.id, template)
        return to_workflow_run_detail_read(run, tasks)

    def get_workflow(self, run_id: str) -> WorkflowRunDetailRead:
        run = self._workflow_repository.get_run(run_id)
        if run is None:
            raise WorkflowRunNotFoundError
        tasks = self._workflow_repository.list_task_nodes(run.id)
        return to_workflow_run_detail_read(run, tasks)

    def advance_workflow(self, run_id: str, *, approve: bool) -> WorkflowRunDetailRead:
        run = self._workflow_repository.get_run(run_id)
        if run is None:
            raise WorkflowRunNotFoundError

        stage_order_value = run.state_json.get("stage_order", [])
        stage_order_raw = stage_order_value if isinstance(stage_order_value, list) else []
        stage_order = [stage for stage in stage_order_raw if isinstance(stage, str)]
        if not stage_order or run.current_stage not in stage_order:
            raise WorkflowRunNotFoundError

        tasks = self._workflow_repository.list_task_nodes(run.id)
        task_by_name = {task.name: task for task in tasks}
        current_task = task_by_name[run.current_stage]

        if run.status is WorkflowRunStatus.NEEDS_APPROVAL:
            if not approve:
                raise WorkflowApprovalRequiredError(to_workflow_run_detail_read(run, tasks))

            self._workflow_repository.update_task_node(
                current_task, status=TaskNodeStatus.IN_PROGRESS
            )
            updated_run = self._workflow_repository.update_run(
                run,
                status=WorkflowRunStatus.RUNNING,
                current_stage=run.current_stage,
                state={**dict(run.state_json), "current_stage": run.current_stage},
                last_error=None,
                ended_at=run.ended_at,
            )
            refreshed_tasks = self._workflow_repository.list_task_nodes(updated_run.id)
            return to_workflow_run_detail_read(updated_run, refreshed_tasks)

        current_index = stage_order.index(run.current_stage)
        if current_index >= len(stage_order) - 2:
            return to_workflow_run_detail_read(run, tasks)

        next_stage = stage_order[current_index + 1]
        next_task = task_by_name[next_stage]
        requires_approval = bool(next_task.metadata_json.get("requires_approval", False))

        self._workflow_repository.update_task_node(current_task, status=TaskNodeStatus.COMPLETED)

        if requires_approval and not approve:
            self._workflow_repository.update_task_node(next_task, status=TaskNodeStatus.BLOCKED)
            blocked_run = self._workflow_repository.update_run(
                run,
                status=WorkflowRunStatus.NEEDS_APPROVAL,
                current_stage=next_stage,
                state={**dict(run.state_json), "current_stage": next_stage},
                last_error=None,
                ended_at=run.ended_at,
            )
            refreshed_tasks = self._workflow_repository.list_task_nodes(blocked_run.id)
            raise WorkflowApprovalRequiredError(
                to_workflow_run_detail_read(blocked_run, refreshed_tasks)
            )

        self._workflow_repository.update_task_node(next_task, status=TaskNodeStatus.IN_PROGRESS)
        updated_run = self._workflow_repository.update_run(
            run,
            status=WorkflowRunStatus.RUNNING,
            current_stage=next_stage,
            state={**dict(run.state_json), "current_stage": next_stage},
            last_error=None,
            ended_at=run.ended_at,
        )
        refreshed_tasks = self._workflow_repository.list_task_nodes(updated_run.id)
        return to_workflow_run_detail_read(updated_run, refreshed_tasks)

    def _create_stage_tasks(
        self, workflow_run_id: str, template: WorkflowTemplate
    ) -> list[TaskNode]:
        task_nodes: list[TaskNode] = []
        for index, stage in enumerate(template.stages, start=1):
            task_nodes.append(
                self._workflow_repository.create_task_node(
                    workflow_run_id=workflow_run_id,
                    name=stage.key,
                    node_type=TaskNodeType.STAGE,
                    status=TaskNodeStatus.IN_PROGRESS if index == 1 else TaskNodeStatus.PENDING,
                    sequence=index,
                    parent_id=None,
                    metadata={
                        "title": stage.title,
                        "role": stage.role,
                        "requires_approval": stage.requires_approval,
                    },
                )
            )
        return task_nodes

    def _build_skill_snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "id": record.id,
                "name": record.name,
                "source": record.source.value,
                "scope": record.scope.value,
                "status": record.status.value,
                "compatibility": list(record.compatibility_json),
            }
            for record in self._skill_repository.list_skills()
        ]

    def _build_mcp_snapshot(self) -> list[dict[str, object]]:
        return [
            {
                "id": server.id,
                "name": server.name,
                "source": server.source.value,
                "scope": server.scope.value,
                "transport": server.transport.value,
                "status": server.status.value,
                "enabled": server.enabled,
                "capability_count": len(self._mcp_repository.list_capabilities(server.id)),
            }
            for server in self._mcp_repository.list_servers()
        ]


def get_workflow_service(
    db_session: DBSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> WorkflowService:
    return WorkflowService(db_session, settings=settings)
