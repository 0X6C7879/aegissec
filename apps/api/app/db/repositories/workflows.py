from __future__ import annotations

from datetime import datetime

from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.db.models import (
    GraphEdge,
    GraphNode,
    GraphType,
    TaskNode,
    TaskNodeStatus,
    TaskNodeType,
    WorkflowRun,
    WorkflowRunStatus,
    utc_now,
)

_ACTIVE_WORKFLOW_RUN_STATUSES = frozenset(
    {
        WorkflowRunStatus.QUEUED,
        WorkflowRunStatus.RUNNING,
        WorkflowRunStatus.NEEDS_APPROVAL,
        WorkflowRunStatus.PAUSED,
    }
)


_GRAPH_TYPE_SORT_ORDER = {
    GraphType.TASK: 0,
    GraphType.EVIDENCE: 1,
    GraphType.CAUSAL: 2,
}


class WorkflowRepository:
    def __init__(self, db_session: DBSession):
        self.db_session = db_session

    def create_run(
        self,
        *,
        session_id: str,
        template_name: str,
        status: WorkflowRunStatus,
        current_stage: str | None,
        started_at: datetime,
        ended_at: datetime | None,
        state: dict[str, object],
        last_error: str | None,
    ) -> WorkflowRun:
        created_at = utc_now()
        if status in _ACTIVE_WORKFLOW_RUN_STATUSES:
            for existing_run in self._list_active_runs_for_session(session_id):
                existing_run.status = WorkflowRunStatus.BLOCKED
                existing_run.ended_at = started_at
                existing_run.updated_at = started_at
                self.db_session.add(existing_run)

        run = WorkflowRun(
            session_id=session_id,
            template_name=template_name,
            status=status,
            current_stage=current_stage,
            state_json=state,
            last_error=last_error,
            created_at=created_at,
            updated_at=created_at,
            started_at=started_at,
            ended_at=ended_at,
        )
        self.db_session.add(run)
        self.db_session.commit()
        self.db_session.refresh(run)
        return run

    def get_run(self, run_id: str) -> WorkflowRun | None:
        return self.db_session.get(WorkflowRun, run_id)

    def update_run(
        self,
        run: WorkflowRun,
        *,
        status: WorkflowRunStatus | None = None,
        current_stage: str | None = None,
        state: dict[str, object] | None = None,
        last_error: str | None = None,
        ended_at: datetime | None = None,
    ) -> WorkflowRun:
        if status is not None:
            run.status = status
        if current_stage is not None:
            run.current_stage = current_stage
        if state is not None:
            run.state_json = state
        run.last_error = last_error
        run.ended_at = ended_at
        run.updated_at = utc_now()
        self.db_session.add(run)
        self.db_session.commit()
        self.db_session.refresh(run)
        return run

    def get_active_run_for_session(self, session_id: str) -> WorkflowRun | None:
        statement = (
            select(WorkflowRun)
            .where(WorkflowRun.session_id == session_id)
            .where(col(WorkflowRun.status).in_(_ACTIVE_WORKFLOW_RUN_STATUSES))
            .order_by(col(WorkflowRun.started_at).desc(), col(WorkflowRun.id).desc())
        )
        return self.db_session.exec(statement).first()

    def list_runs_for_session(self, session_id: str) -> list[WorkflowRun]:
        statement = (
            select(WorkflowRun)
            .where(WorkflowRun.session_id == session_id)
            .order_by(col(WorkflowRun.started_at).desc(), col(WorkflowRun.id).desc())
        )
        return list(self.db_session.exec(statement).all())

    def create_task_node(
        self,
        *,
        workflow_run_id: str,
        name: str,
        node_type: TaskNodeType,
        status: TaskNodeStatus,
        sequence: int,
        parent_id: str | None,
        metadata: dict[str, object],
    ) -> TaskNode:
        task_node = TaskNode(
            workflow_run_id=workflow_run_id,
            name=name,
            node_type=node_type,
            status=status,
            sequence=sequence,
            parent_id=parent_id,
            metadata_json=metadata,
        )
        self.db_session.add(task_node)
        self.db_session.commit()
        self.db_session.refresh(task_node)
        return task_node

    def list_task_nodes(self, workflow_run_id: str) -> list[TaskNode]:
        statement = (
            select(TaskNode)
            .where(TaskNode.workflow_run_id == workflow_run_id)
            .order_by(
                col(TaskNode.sequence).asc(), col(TaskNode.created_at).asc(), col(TaskNode.id).asc()
            )
        )
        return list(self.db_session.exec(statement).all())

    def update_task_node(self, task_node: TaskNode, *, status: TaskNodeStatus) -> TaskNode:
        task_node.status = status
        self.db_session.add(task_node)
        self.db_session.commit()
        self.db_session.refresh(task_node)
        return task_node

    def patch_task_node(
        self,
        task_node: TaskNode,
        *,
        status: TaskNodeStatus | None = None,
        parent_id: str | None = None,
        update_parent: bool = False,
        metadata: dict[str, object] | None = None,
    ) -> TaskNode:
        if status is not None:
            task_node.status = status
        if update_parent:
            task_node.parent_id = parent_id
        if metadata is not None:
            task_node.metadata_json = metadata
        self.db_session.add(task_node)
        self.db_session.commit()
        self.db_session.refresh(task_node)
        return task_node

    def _list_active_runs_for_session(self, session_id: str) -> list[WorkflowRun]:
        statement = (
            select(WorkflowRun)
            .where(WorkflowRun.session_id == session_id)
            .where(col(WorkflowRun.status).in_(_ACTIVE_WORKFLOW_RUN_STATUSES))
        )
        return list(self.db_session.exec(statement).all())


class GraphRepository:
    def __init__(self, db_session: DBSession):
        self.db_session = db_session

    def create_node(
        self,
        *,
        session_id: str,
        workflow_run_id: str,
        graph_type: GraphType,
        node_type: str,
        label: str,
        payload: dict[str, object],
        stable_key: str,
    ) -> GraphNode:
        node = GraphNode(
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            graph_type=graph_type,
            node_type=node_type,
            label=label,
            payload_json=payload,
            stable_key=stable_key,
            created_at=utc_now(),
        )
        self.db_session.add(node)
        self.db_session.commit()
        self.db_session.refresh(node)
        return node

    def patch_node(
        self,
        node: GraphNode,
        *,
        label: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> GraphNode:
        if label is not None:
            node.label = label
        if payload is not None:
            node.payload_json = payload
        self.db_session.add(node)
        self.db_session.commit()
        self.db_session.refresh(node)
        return node

    def list_nodes(
        self,
        session_id: str,
        *,
        workflow_run_id: str | None = None,
        graph_type: GraphType | None = None,
    ) -> list[GraphNode]:
        statement = select(GraphNode).where(GraphNode.session_id == session_id)
        if workflow_run_id is not None:
            statement = statement.where(GraphNode.workflow_run_id == workflow_run_id)
        if graph_type is not None:
            statement = statement.where(GraphNode.graph_type == graph_type)

        nodes = list(self.db_session.exec(statement).all())
        return sorted(
            nodes,
            key=lambda node: (
                _GRAPH_TYPE_SORT_ORDER.get(node.graph_type, 99),
                node.stable_key,
                node.created_at,
                node.id,
            ),
        )

    def get_node_by_stable_key(
        self, session_id: str, stable_key: str, *, workflow_run_id: str | None = None
    ) -> GraphNode | None:
        statement = (
            select(GraphNode)
            .where(GraphNode.session_id == session_id)
            .where(GraphNode.stable_key == stable_key)
        )
        if workflow_run_id is not None:
            statement = statement.where(GraphNode.workflow_run_id == workflow_run_id)
        nodes = list(self.db_session.exec(statement).all())
        if not nodes:
            return None
        return sorted(
            nodes,
            key=lambda node: (_GRAPH_TYPE_SORT_ORDER.get(node.graph_type, 99), node.id),
        )[0]

    def create_edge(
        self,
        *,
        session_id: str,
        workflow_run_id: str,
        graph_type: GraphType,
        source_node_id: str,
        target_node_id: str,
        relation: str,
        payload: dict[str, object],
        stable_key: str,
    ) -> GraphEdge:
        edge = GraphEdge(
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            graph_type=graph_type,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
            relation=relation,
            payload_json=payload,
            stable_key=stable_key,
            created_at=utc_now(),
        )
        self.db_session.add(edge)
        self.db_session.commit()
        self.db_session.refresh(edge)
        return edge

    def list_edges(
        self,
        session_id: str,
        *,
        workflow_run_id: str | None = None,
        graph_type: GraphType | None = None,
    ) -> list[GraphEdge]:
        statement = select(GraphEdge).where(GraphEdge.session_id == session_id)
        if workflow_run_id is not None:
            statement = statement.where(GraphEdge.workflow_run_id == workflow_run_id)
        if graph_type is not None:
            statement = statement.where(GraphEdge.graph_type == graph_type)

        edges = list(self.db_session.exec(statement).all())
        return sorted(
            edges,
            key=lambda edge: (
                _GRAPH_TYPE_SORT_ORDER.get(edge.graph_type, 99),
                edge.stable_key,
                edge.created_at,
                edge.id,
            ),
        )

    def get_edge_by_stable_key(
        self, session_id: str, stable_key: str, *, workflow_run_id: str | None = None
    ) -> GraphEdge | None:
        statement = (
            select(GraphEdge)
            .where(GraphEdge.session_id == session_id)
            .where(GraphEdge.stable_key == stable_key)
        )
        if workflow_run_id is not None:
            statement = statement.where(GraphEdge.workflow_run_id == workflow_run_id)
        edges = list(self.db_session.exec(statement).all())
        if not edges:
            return None
        return sorted(
            edges,
            key=lambda edge: (_GRAPH_TYPE_SORT_ORDER.get(edge.graph_type, 99), edge.id),
        )[0]
