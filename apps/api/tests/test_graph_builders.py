from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from app.db.models import Session, TaskNodeStatus, TaskNodeType, WorkflowRunStatus
from app.db.repositories import WorkflowRepository
from app.graphs.builders import CausalGraphBuilder, TaskGraphBuilder


def test_task_graph_builder_projects_ordered_stage_nodes_and_edges() -> None:
    with _db_session() as db_session:
        session = _create_session(db_session)
        workflow_repository = WorkflowRepository(db_session)
        run = workflow_repository.create_run(
            session_id=session.id,
            template_name="authorized-assessment",
            status=WorkflowRunStatus.RUNNING,
            current_stage="scope_guard",
            started_at=datetime(2026, 3, 29, 13, 0, tzinfo=UTC),
            ended_at=None,
            state={"current_stage": "scope_guard", "findings": []},
            last_error=None,
        )
        first = workflow_repository.create_task_node(
            workflow_run_id=run.id,
            name="scope_guard",
            node_type=TaskNodeType.STAGE,
            status=TaskNodeStatus.IN_PROGRESS,
            sequence=1,
            parent_id=None,
            metadata={"title": "范围确认", "role": "coordinator"},
        )
        second = workflow_repository.create_task_node(
            workflow_run_id=run.id,
            name="runtime_boot",
            node_type=TaskNodeType.STAGE,
            status=TaskNodeStatus.PENDING,
            sequence=2,
            parent_id=None,
            metadata={"title": "环境启动", "role": "operator"},
        )

        graph = TaskGraphBuilder().build(run=run, tasks=[first, second])

        assert graph.graph_type == "task"
        assert graph.workflow_run_id == run.id
        assert graph.current_stage == "scope_guard"
        assert [node.id for node in graph.nodes] == [first.id, second.id]
        assert [node.label for node in graph.nodes] == ["范围确认", "环境启动"]
        assert graph.nodes[0].data["status"] == "in_progress"
        assert graph.nodes[0].data["current"] is True
        assert len(graph.edges) == 1
        assert graph.edges[0].source == first.id
        assert graph.edges[0].target == second.id
        assert graph.edges[0].relation == "precedes"


def test_causal_graph_builder_projects_findings_relationships() -> None:
    with _db_session() as db_session:
        session = _create_session(db_session)
        workflow_repository = WorkflowRepository(db_session)
        run = workflow_repository.create_run(
            session_id=session.id,
            template_name="authorized-assessment",
            status=WorkflowRunStatus.RUNNING,
            current_stage="findings_merge",
            started_at=datetime(2026, 3, 29, 13, 30, tzinfo=UTC),
            ended_at=None,
            state={
                "current_stage": "findings_merge",
                "findings": [
                    {
                        "id": "finding-auth",
                        "title": "Weak auth boundary",
                        "kind": "finding",
                        "supports": ["finding-impact"],
                    },
                    {
                        "id": "finding-impact",
                        "title": "Privilege exposure",
                        "kind": "impact",
                    },
                ],
            },
            last_error=None,
        )

        graph = CausalGraphBuilder().build(run=run)

        assert graph.graph_type == "causal"
        assert graph.workflow_run_id == run.id
        assert graph.current_stage == "findings_merge"
        assert [node.id for node in graph.nodes] == ["finding-auth", "finding-impact"]
        assert [node.label for node in graph.nodes] == ["Weak auth boundary", "Privilege exposure"]
        assert len(graph.edges) == 1
        assert graph.edges[0].source == "finding-auth"
        assert graph.edges[0].target == "finding-impact"
        assert graph.edges[0].relation == "supports"


def _db_session() -> DBSession:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return DBSession(engine)


def _create_session(db_session: DBSession) -> Session:
    session = Session(title="Graph Test Session")
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session
