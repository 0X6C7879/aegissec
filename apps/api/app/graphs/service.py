from __future__ import annotations

from fastapi import Depends
from sqlmodel import Session as DBSession

from app.core.settings import Settings, get_settings
from app.db.models import GraphType, SessionGraphRead
from app.db.repositories import SessionRepository, WorkflowRepository
from app.db.session import get_db_session
from app.graphs.builders import CausalGraphBuilder, TaskGraphBuilder


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
    ) -> None:
        del settings
        self._session_repository = SessionRepository(db_session)
        self._workflow_repository = WorkflowRepository(db_session)
        self._task_graph_builder = task_graph_builder or TaskGraphBuilder()
        self._causal_graph_builder = causal_graph_builder or CausalGraphBuilder()

    def get_graph(self, *, session_id: str, graph_type: GraphType) -> SessionGraphRead:
        session = self._session_repository.get_session(session_id)
        if session is None:
            raise SessionNotFoundError

        run = self._workflow_repository.get_active_run_for_session(session_id)
        if run is None:
            runs = self._workflow_repository.list_runs_for_session(session_id)
            run = runs[0] if runs else None
        if run is None:
            raise WorkflowGraphNotFoundError

        if graph_type is GraphType.TASK:
            tasks = self._workflow_repository.list_task_nodes(run.id)
            return self._task_graph_builder.build(run=run, tasks=tasks)

        return self._causal_graph_builder.build(run=run)


def get_graph_service(
    db_session: DBSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> GraphService:
    return GraphService(db_session, settings=settings)
