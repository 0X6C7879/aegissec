from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from app.db.models import GraphType, Session, TaskNodeStatus, TaskNodeType, WorkflowRunStatus
from app.db.repositories import GraphRepository, WorkflowRepository


def test_workflow_slice_persists_runs_and_task_nodes_in_stable_order() -> None:
    with _db_session() as db_session:
        session = _create_session(db_session)
        repository = WorkflowRepository(db_session)

        older_run = repository.create_run(
            session_id=session.id,
            template_name="recon",
            status=WorkflowRunStatus.QUEUED,
            current_stage="queued",
            started_at=datetime(2026, 3, 29, 8, 0, tzinfo=UTC),
            ended_at=None,
            state={"current_stage": "queued"},
            last_error=None,
        )
        newer_run = repository.create_run(
            session_id=session.id,
            template_name="validation",
            status=WorkflowRunStatus.RUNNING,
            current_stage="execute",
            started_at=datetime(2026, 3, 29, 9, 0, tzinfo=UTC),
            ended_at=None,
            state={"current_stage": "execute"},
            last_error=None,
        )

        repository.create_task_node(
            workflow_run_id=newer_run.id,
            name="collect-evidence",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.IN_PROGRESS,
            sequence=2,
            parent_id=None,
            metadata={"tool": "nmap"},
        )
        repository.create_task_node(
            workflow_run_id=newer_run.id,
            name="plan",
            node_type=TaskNodeType.STAGE,
            status=TaskNodeStatus.COMPLETED,
            sequence=1,
            parent_id=None,
            metadata={"tool": "planner"},
        )

        runs = repository.list_runs_for_session(session.id)
        tasks = repository.list_task_nodes(newer_run.id)

        assert [run.id for run in runs] == [newer_run.id, older_run.id]
        assert [task.name for task in tasks] == ["plan", "collect-evidence"]
        assert tasks[0].metadata_json == {"tool": "planner"}


def test_graph_slice_reads_nodes_and_edges_deterministically() -> None:
    with _db_session() as db_session:
        session = _create_session(db_session)
        workflow_repository = WorkflowRepository(db_session)
        repository = GraphRepository(db_session)
        run = workflow_repository.create_run(
            session_id=session.id,
            template_name="graph-build",
            status=WorkflowRunStatus.RUNNING,
            current_stage="graph",
            started_at=datetime(2026, 3, 29, 10, 0, tzinfo=UTC),
            ended_at=None,
            state={"current_stage": "graph"},
            last_error=None,
        )

        second_node = repository.create_node(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.CAUSAL,
            node_type="finding",
            label="Impact",
            payload={"confidence": "high"},
            stable_key="finding-impact",
        )
        first_node = repository.create_node(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.TASK,
            node_type="task",
            label="Enumerate",
            payload={"status": "ready"},
            stable_key="task-enumerate",
        )

        repository.create_edge(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.CAUSAL,
            source_node_id=second_node.id,
            target_node_id=first_node.id,
            relation="supports",
            payload={"kind": "causal"},
            stable_key="edge-causal",
        )
        first_edge = repository.create_edge(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.TASK,
            source_node_id=first_node.id,
            target_node_id=second_node.id,
            relation="depends_on",
            payload={"kind": "task"},
            stable_key="edge-task",
        )

        all_nodes = repository.list_nodes(session.id, workflow_run_id=run.id)
        all_edges = repository.list_edges(session.id, workflow_run_id=run.id)
        fetched_edge = repository.get_edge_by_stable_key(session.id, "edge-task")

        assert [node.stable_key for node in all_nodes] == ["task-enumerate", "finding-impact"]
        assert [edge.stable_key for edge in all_edges] == ["edge-task", "edge-causal"]
        assert fetched_edge is not None
        assert fetched_edge.id == first_edge.id


def _db_session() -> DBSession:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return DBSession(engine)


def _create_session(db_session: DBSession) -> Session:
    session = Session(title="Workflow Graph Slice")
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session
