from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from app.db.models import GraphType, Session, TaskNodeStatus, TaskNodeType, WorkflowRunStatus
from app.db.repositories import GraphRepository, WorkflowRepository
from app.graphs.builders import AttackGraphBuilder, CausalGraphBuilder, TaskGraphBuilder


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
            metadata={"title": "范围确认", "role": "coordinator", "summary": "stage summary"},
        )
        second = workflow_repository.create_task_node(
            workflow_run_id=run.id,
            name="runtime_boot",
            node_type=TaskNodeType.STAGE,
            status=TaskNodeStatus.PENDING,
            sequence=2,
            parent_id=None,
            metadata={
                "title": "环境启动",
                "role": "operator",
                "depends_on_task_ids": [first.id],
                "summary": "boot summary",
                "evidence_confidence": 0.7,
            },
        )

        graph = TaskGraphBuilder().build(run=run, tasks=[first, second])

        assert graph.graph_type == "task"
        assert graph.workflow_run_id == run.id
        assert graph.current_stage == "scope_guard"
        assert [node.id for node in graph.nodes] == [first.id, second.id]
        assert [node.label for node in graph.nodes] == ["范围确认", "环境启动"]
        assert graph.nodes[0].data["status"] == "in_progress"
        assert graph.nodes[0].data["current"] is True
        assert graph.nodes[0].data["summary"] == "stage summary"
        assert graph.nodes[1].data["evidence_confidence"] == 0.7
        assert len(graph.edges) == 1
        assert graph.edges[0].source == first.id
        assert graph.edges[0].target == second.id
        assert graph.edges[0].relation == "depends_on"


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
                        "summary": "Authentication controls can be bypassed",
                        "confidence": 0.82,
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
        assert graph.nodes[0].data["summary"] == "Authentication controls can be bypassed"
        assert graph.nodes[0].data["confidence"] == 0.82
        assert len(graph.edges) == 1
        assert graph.edges[0].source == "finding-auth"
        assert graph.edges[0].target == "finding-impact"
        assert graph.edges[0].relation == "supports"


def test_attack_graph_builder_unifies_goal_tasks_observations_and_findings() -> None:
    with _db_session() as db_session:
        session = _create_session(db_session)
        workflow_repository = WorkflowRepository(db_session)
        graph_repository = GraphRepository(db_session)
        run = workflow_repository.create_run(
            session_id=session.id,
            template_name="authorized-assessment",
            status=WorkflowRunStatus.RUNNING,
            current_stage="safe_validation",
            started_at=datetime(2026, 3, 29, 14, 0, tzinfo=UTC),
            ended_at=None,
            state={
                "goal": "Confirm the lowest-risk attack path inside authorized scope.",
                "current_stage": "safe_validation",
                "seed_message_id": "message-seed-1",
                "hypothesis_updates": [
                    {
                        "kind": "validation",
                        "trace_id": "trace-attack-1",
                        "task": "safe_validation.validate_primary_hypothesis",
                        "result": "supported",
                    }
                ],
            },
            last_error=None,
        )
        collect = workflow_repository.create_task_node(
            workflow_run_id=run.id,
            name="context_collect.attack_surface",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.COMPLETED,
            sequence=1,
            parent_id=None,
            metadata={
                "title": "攻击面清点",
                "stage_key": "context_collect",
                "summary": "Collected ingress points.",
            },
        )
        validate = workflow_repository.create_task_node(
            workflow_run_id=run.id,
            name="safe_validation.validate_primary_hypothesis",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.IN_PROGRESS,
            sequence=2,
            parent_id=None,
            metadata={
                "title": "低风险验证执行",
                "stage_key": "safe_validation",
                "summary": "Validated reachable path.",
                "depends_on_task_ids": [collect.id],
            },
        )
        evidence_node = graph_repository.create_node(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.EVIDENCE,
            node_type="execution",
            label="攻击面清点",
            payload={
                "trace_id": "trace-attack-1",
                "task_id": collect.id,
                "status": "completed",
                "summary": "Observed exposed admin surface.",
            },
            stable_key="evidence-node:trace-attack-1",
        )
        finding_node = graph_repository.create_node(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.CAUSAL,
            node_type="finding",
            label="Weak auth boundary",
            payload={
                "id": "finding-auth",
                "title": "Weak auth boundary",
                "kind": "finding",
                "trace_id": "trace-attack-1",
                "task": validate.name,
            },
            stable_key="causal-node:finding-auth",
        )
        impact_node = graph_repository.create_node(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.CAUSAL,
            node_type="impact",
            label="Privilege exposure",
            payload={
                "id": "finding-impact",
                "title": "Privilege exposure",
                "kind": "impact",
            },
            stable_key="causal-node:finding-impact",
        )
        graph_repository.create_edge(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.CAUSAL,
            source_node_id=finding_node.id,
            target_node_id=impact_node.id,
            relation="supports",
            payload={},
            stable_key="causal-edge:finding-auth:supports:finding-impact",
        )

        graph = AttackGraphBuilder().build(
            run=run,
            tasks=[collect, validate],
            evidence_nodes=[evidence_node],
            evidence_edges=[],
            causal_nodes=[finding_node, impact_node],
            causal_edges=graph_repository.list_edges(
                session.id,
                workflow_run_id=run.id,
                graph_type=GraphType.CAUSAL,
            ),
        )

        assert graph.graph_type == "attack"
        assert graph.workflow_run_id == run.id
        node_ids = {node.id for node in graph.nodes}
        assert f"goal:{run.id}" in node_ids
        assert collect.id in node_ids
        assert validate.id in node_ids
        assert "trace-attack-1" in node_ids
        assert "finding-auth" in node_ids
        assert "finding-impact" in node_ids
        assert f"outcome:{run.id}" in node_ids
        node_types = {node.id: node.node_type for node in graph.nodes}
        assert node_types[collect.id] == "surface"
        assert node_types[validate.id] == "exploit"
        assert node_types["trace-attack-1"] == "observation"
        assert node_types["finding-auth"] == "vulnerability"
        assert node_types["finding-impact"] == "outcome"
        anchor_map = {node.id: node.data.get("source_message_id") for node in graph.nodes}
        assert anchor_map[collect.id] == "message-seed-1"
        assert anchor_map["trace-attack-1"] == "message-seed-1"
        assert anchor_map["finding-auth"] == "message-seed-1"
        relations = {(edge.source, edge.relation, edge.target) for edge in graph.edges}
        assert (collect.id, "enables", validate.id) in relations
        assert (collect.id, "discovers", "trace-attack-1") in relations
        assert ("trace-attack-1", "confirms", "finding-auth") in relations
        assert ("finding-auth", "supports", "finding-impact") in relations


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
