from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from app.db.models import (
    GraphType,
    Session,
    TaskNodeStatus,
    TaskNodeType,
    WorkflowRunStatus,
)
from app.db.repositories import GraphRepository, WorkflowRepository


def test_workflow_repository_creates_lists_and_orders_runs() -> None:
    with _db_session() as db_session:
        session = _create_session(db_session)
        repository = WorkflowRepository(db_session)
        base_time = datetime(2026, 3, 29, 8, 0, tzinfo=UTC)

        first_run = repository.create_run(
            session_id=session.id,
            template_name="recon",
            status=WorkflowRunStatus.DONE,
            current_stage="finished",
            started_at=base_time,
            ended_at=base_time + timedelta(minutes=5),
            state={"stage": "finished"},
            last_error=None,
        )
        second_run = repository.create_run(
            session_id=session.id,
            template_name="exploit-validation",
            status=WorkflowRunStatus.ERROR,
            current_stage="failed",
            started_at=base_time + timedelta(minutes=10),
            ended_at=base_time + timedelta(minutes=11),
            state={"stage": "failed"},
            last_error="tool error",
        )

        runs = repository.list_runs_for_session(session.id)

        assert [run.id for run in runs] == [second_run.id, first_run.id]
        assert [run.template_name for run in runs] == ["exploit-validation", "recon"]


def test_workflow_repository_enforces_one_active_run_per_session() -> None:
    with _db_session() as db_session:
        session = _create_session(db_session)
        repository = WorkflowRepository(db_session)
        base_time = datetime(2026, 3, 29, 9, 0, tzinfo=UTC)

        active_run = repository.create_run(
            session_id=session.id,
            template_name="approval-flow",
            status=WorkflowRunStatus.RUNNING,
            current_stage="dispatch",
            started_at=base_time,
            ended_at=None,
            state={"stage": "dispatch"},
            last_error=None,
        )
        replacement_run = repository.create_run(
            session_id=session.id,
            template_name="follow-up",
            status=WorkflowRunStatus.NEEDS_APPROVAL,
            current_stage="approval",
            started_at=base_time + timedelta(minutes=1),
            ended_at=None,
            state={"stage": "approval"},
            last_error=None,
        )

        refreshed_first_run = repository.get_run(active_run.id)
        active_for_session = repository.get_active_run_for_session(session.id)

        assert refreshed_first_run is not None
        assert refreshed_first_run.status == WorkflowRunStatus.BLOCKED
        assert active_for_session is not None
        assert active_for_session.id == replacement_run.id


def test_workflow_repository_creates_and_orders_task_nodes() -> None:
    with _db_session() as db_session:
        session = _create_session(db_session)
        repository = WorkflowRepository(db_session)
        run = repository.create_run(
            session_id=session.id,
            template_name="graph-build",
            status=WorkflowRunStatus.RUNNING,
            current_stage="tasks",
            started_at=datetime(2026, 3, 29, 10, 0, tzinfo=UTC),
            ended_at=None,
            state={"stage": "tasks"},
            last_error=None,
        )

        second_task = repository.create_task_node(
            workflow_run_id=run.id,
            name="collect-evidence",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.READY,
            sequence=2,
            parent_id=None,
            metadata={"step": 2},
        )
        first_task = repository.create_task_node(
            workflow_run_id=run.id,
            name="plan",
            node_type=TaskNodeType.STAGE,
            status=TaskNodeStatus.COMPLETED,
            sequence=1,
            parent_id=None,
            metadata={"step": 1},
        )

        tasks = repository.list_task_nodes(run.id)

        assert [task.id for task in tasks] == [first_task.id, second_task.id]
        assert [task.sequence for task in tasks] == [1, 2]
        assert tasks[0].metadata_json == {"step": 1}


def test_graph_repository_stores_and_lists_graph_data_deterministically() -> None:
    with _db_session() as db_session:
        session = _create_session(db_session)
        workflow_repository = WorkflowRepository(db_session)
        repository = GraphRepository(db_session)
        run = workflow_repository.create_run(
            session_id=session.id,
            template_name="graph-build",
            status=WorkflowRunStatus.RUNNING,
            current_stage="graph",
            started_at=datetime(2026, 3, 29, 11, 0, tzinfo=UTC),
            ended_at=None,
            state={"stage": "graph"},
            last_error=None,
        )

        later_node = repository.create_node(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.CAUSAL,
            node_type="impact",
            label="Escalated privileges",
            payload={"severity": "high"},
            stable_key="impact-node",
        )
        earlier_node = repository.create_node(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.TASK,
            node_type="task",
            label="Enumerate target",
            payload={"stage": 1},
            stable_key="task-node",
        )

        later_edge = repository.create_edge(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.CAUSAL,
            source_node_id=later_node.id,
            target_node_id=earlier_node.id,
            relation="enabled_by",
            payload={"confidence": "medium"},
            stable_key="causal-edge",
        )
        earlier_edge = repository.create_edge(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.TASK,
            source_node_id=earlier_node.id,
            target_node_id=later_node.id,
            relation="precedes",
            payload={"order": 1},
            stable_key="task-edge",
        )

        task_nodes = repository.list_nodes(
            session.id, workflow_run_id=run.id, graph_type=GraphType.TASK
        )
        all_nodes = repository.list_nodes(session.id, workflow_run_id=run.id)
        all_edges = repository.list_edges(session.id, workflow_run_id=run.id)
        fetched_edge = repository.get_edge_by_stable_key(session.id, "task-edge")

        assert [node.id for node in task_nodes] == [earlier_node.id]
        assert [node.stable_key for node in all_nodes] == ["task-node", "impact-node"]
        assert [edge.id for edge in all_edges] == [earlier_edge.id, later_edge.id]
        assert fetched_edge is not None
        assert fetched_edge.id == earlier_edge.id


def test_workflow_repository_persists_state_and_isolates_graphs_by_run() -> None:
    with _db_session() as db_session:
        session = _create_session(db_session)
        workflow_repository = WorkflowRepository(db_session)
        graph_repository = GraphRepository(db_session)
        base_time = datetime(2026, 3, 29, 12, 0, tzinfo=UTC)

        first_run = workflow_repository.create_run(
            session_id=session.id,
            template_name="authorized-assessment",
            status=WorkflowRunStatus.QUEUED,
            current_stage="scope_guard",
            started_at=base_time,
            ended_at=None,
            state={"messages": [], "current_stage": "scope_guard"},
            last_error=None,
        )
        second_run = workflow_repository.create_run(
            session_id=session.id,
            template_name="authorized-assessment",
            status=WorkflowRunStatus.RUNNING,
            current_stage="context_collect",
            started_at=base_time + timedelta(minutes=1),
            ended_at=None,
            state={"messages": [{"role": "user", "content": "collect"}]},
            last_error="upstream timeout",
        )

        graph_repository.create_node(
            session_id=session.id,
            workflow_run_id=first_run.id,
            graph_type=GraphType.TASK,
            node_type="task",
            label="first-run-node",
            payload={"run": 1},
            stable_key="first-run-node",
        )
        graph_repository.create_node(
            session_id=session.id,
            workflow_run_id=second_run.id,
            graph_type=GraphType.TASK,
            node_type="task",
            label="second-run-node",
            payload={"run": 2},
            stable_key="second-run-node",
        )

        stored_second_run = workflow_repository.get_run(second_run.id)
        first_run_nodes = graph_repository.list_nodes(session.id, workflow_run_id=first_run.id)
        second_run_nodes = graph_repository.list_nodes(session.id, workflow_run_id=second_run.id)

        assert stored_second_run is not None
        assert stored_second_run.state_json == {
            "messages": [{"role": "user", "content": "collect"}]
        }
        assert stored_second_run.last_error == "upstream timeout"
        assert [node.label for node in first_run_nodes] == ["first-run-node"]
        assert [node.label for node in second_run_nodes] == ["second-run-node"]


def _db_session() -> DBSession:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return DBSession(engine)


def _create_session(db_session: DBSession) -> Session:
    session = Session(title="Workflow Test Session")
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)
    return session
