from __future__ import annotations

from fastapi import Depends
from sqlmodel import Session as DBSession

from app.core.settings import Settings, get_settings
from app.db.models import GraphType, SessionGraphRead, TaskNode, WorkflowRun
from app.db.repositories import GraphRepository, SessionRepository, WorkflowRepository
from app.db.session import get_db_session
from app.graphs.builders import (
    AttackGraphBuilder,
    CausalGraphBuilder,
    SnapshotGraphBuilder,
    TaskGraphBuilder,
)


class SessionNotFoundError(Exception):
    pass


class WorkflowGraphNotFoundError(Exception):
    pass


class GraphService:
    def __init__(
        self,
        db_session: DBSession,
        *,
        settings: Settings,
        task_graph_builder: TaskGraphBuilder | None = None,
        causal_graph_builder: CausalGraphBuilder | None = None,
        attack_graph_builder: AttackGraphBuilder | None = None,
        snapshot_graph_builder: SnapshotGraphBuilder | None = None,
    ) -> None:
        del settings
        self._session_repository = SessionRepository(db_session)
        self._workflow_repository = WorkflowRepository(db_session)
        self._graph_repository = GraphRepository(db_session)
        self._task_graph_builder = task_graph_builder or TaskGraphBuilder()
        self._causal_graph_builder = causal_graph_builder or CausalGraphBuilder()
        self._attack_graph_builder = attack_graph_builder or AttackGraphBuilder()
        self._snapshot_graph_builder = snapshot_graph_builder or SnapshotGraphBuilder()

    def get_graph(self, *, session_id: str, graph_type: GraphType) -> SessionGraphRead:
        session = self._session_repository.get_session(session_id)
        if session is None:
            raise SessionNotFoundError

        if graph_type is GraphType.ATTACK:
            return self._attack_graph_builder.build_from_conversation(
                session=session,
                messages=self._session_repository.list_messages(session_id),
                generations=self._session_repository.list_generations(session_id),
            )

        run = self._workflow_repository.get_active_run_for_session(session_id)
        if run is None:
            runs = self._workflow_repository.list_runs_for_session(session_id)
            run = runs[0] if runs else None
        if run is None:
            return SessionGraphRead(
                session_id=session_id,
                workflow_run_id="",
                graph_type=graph_type,
                current_stage=None,
                nodes=[],
                edges=[],
            )

        graph_nodes = self._graph_repository.list_nodes(
            session_id,
            workflow_run_id=run.id,
            graph_type=graph_type,
        )
        graph_edges = self._graph_repository.list_edges(
            session_id,
            workflow_run_id=run.id,
            graph_type=graph_type,
        )
        if graph_nodes or graph_edges:
            return self._snapshot_graph_builder.build(
                session_id=session_id,
                workflow_run_id=run.id,
                graph_type=graph_type,
                current_stage=run.current_stage,
                nodes=graph_nodes,
                edges=graph_edges,
            )

        tasks = self._workflow_repository.list_task_nodes(run.id)
        return self._build_dynamic_graph(run=run, tasks=tasks, graph_type=graph_type)

    def get_graph_for_run(self, *, run_id: str, graph_type: GraphType) -> SessionGraphRead:
        run = self._workflow_repository.get_run(run_id)
        if run is None:
            raise WorkflowGraphNotFoundError

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

        tasks = self._workflow_repository.list_task_nodes(run.id)
        return self._build_dynamic_graph(run=run, tasks=tasks, graph_type=graph_type)

    def _build_dynamic_graph(
        self,
        *,
        run: WorkflowRun,
        tasks: list[TaskNode],
        graph_type: GraphType,
    ) -> SessionGraphRead:
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


def get_graph_service(
    db_session: DBSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> GraphService:
    return GraphService(db_session, settings=settings)
