from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from app.core.settings import Settings
from app.db.models import (
    AssistantTranscriptSegment,
    AssistantTranscriptSegmentKind,
    GenerationStatus,
    GraphType,
    MessageRole,
    MessageStatus,
    TaskNodeStatus,
    TaskNodeType,
    WorkflowRunStatus,
    assistant_transcript_to_storage,
)
from app.db.repositories import GraphRepository, SessionRepository, WorkflowRepository
from app.graphs.service import GraphService


def test_graph_service_prefers_workflow_attack_graph_when_run_exists() -> None:
    with _db_session() as db_session:
        session_repository = SessionRepository(db_session)
        workflow_repository = WorkflowRepository(db_session)
        session = session_repository.create_session(
            title="Workflow Attack Graph Session",
            goal="Confirm the workflow attack path.",
        )
        branch = session_repository.ensure_active_branch(session)
        _create_conversation_fallback_messages(
            repository=session_repository,
            session_id=session.id,
            branch_id=branch.id,
        )

        run = workflow_repository.create_run(
            session_id=session.id,
            template_name="authorized-assessment",
            status=WorkflowRunStatus.RUNNING,
            current_stage="context_collect",
            started_at=datetime(2026, 4, 6, 13, 0, tzinfo=UTC),
            ended_at=None,
            state={
                "goal": "Confirm the workflow attack path.",
                "current_stage": "context_collect",
            },
            last_error=None,
        )
        task = workflow_repository.create_task_node(
            workflow_run_id=run.id,
            name="context_collect.attack_surface",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.IN_PROGRESS,
            sequence=1,
            parent_id=None,
            metadata={
                "title": "攻击面清点",
                "stage_key": "context_collect",
                "summary": "Collected ingress points.",
                "current": True,
            },
        )

        graph = GraphService(db_session, settings=Settings()).get_graph(
            session_id=session.id,
            graph_type=GraphType.ATTACK,
        )

        node_ids = {node.id for node in graph.nodes}
        assert graph.workflow_run_id == run.id
        assert graph.current_stage == "context_collect"
        assert task.id in node_ids
        assert f"outcome:{run.id}" in node_ids
        assert not any(node_id.startswith("action:message:") for node_id in node_ids)


def test_graph_service_uses_conversation_fallback_without_workflow_run() -> None:
    with _db_session() as db_session:
        session_repository = SessionRepository(db_session)
        session = session_repository.create_session(
            title="Conversation Attack Graph Session",
            goal="确认登录逻辑中的薄弱点",
        )
        branch = session_repository.ensure_active_branch(session)
        _create_conversation_fallback_messages(
            repository=session_repository,
            session_id=session.id,
            branch_id=branch.id,
        )

        graph = GraphService(db_session, settings=Settings()).get_graph(
            session_id=session.id,
            graph_type=GraphType.ATTACK,
        )

        node_types = {node.node_type for node in graph.nodes}
        assert graph.workflow_run_id == ""
        assert {"goal", "action", "outcome"}.issubset(node_types)
        assert "observation" not in node_types
        assert "hypothesis" not in node_types
        assert any(node.id.startswith("action:message:") for node in graph.nodes)


def test_graph_service_prefers_latest_historical_workflow_run_when_no_active_run() -> None:
    with _db_session() as db_session:
        session_repository = SessionRepository(db_session)
        workflow_repository = WorkflowRepository(db_session)
        session = session_repository.create_session(
            title="Historical Workflow Attack Graph Session",
            goal="Use the latest completed workflow graph.",
        )
        branch = session_repository.ensure_active_branch(session)
        _create_conversation_fallback_messages(
            repository=session_repository,
            session_id=session.id,
            branch_id=branch.id,
        )

        run = workflow_repository.create_run(
            session_id=session.id,
            template_name="authorized-assessment",
            status=WorkflowRunStatus.DONE,
            current_stage="context_collect",
            started_at=datetime(2026, 4, 6, 14, 0, tzinfo=UTC),
            ended_at=datetime(2026, 4, 6, 14, 30, tzinfo=UTC),
            state={
                "goal": "Use the latest completed workflow graph.",
                "current_stage": "context_collect",
            },
            last_error=None,
        )
        task = workflow_repository.create_task_node(
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
                "current": True,
            },
        )

        graph = GraphService(db_session, settings=Settings()).get_graph(
            session_id=session.id,
            graph_type=GraphType.ATTACK,
        )

        node_ids = {node.id for node in graph.nodes}
        assert graph.workflow_run_id == run.id
        assert task.id in node_ids
        assert f"outcome:{run.id}" in node_ids
        assert not any(node_id.startswith("action:message:") for node_id in node_ids)


def test_graph_service_prunes_attack_snapshot_before_returning_it() -> None:
    with _db_session() as db_session:
        session_repository = SessionRepository(db_session)
        workflow_repository = WorkflowRepository(db_session)
        graph_repository = GraphRepository(db_session)
        session = session_repository.create_session(
            title="Snapshot Attack Graph Session",
            goal="Prefer the stored attack snapshot.",
        )
        run = workflow_repository.create_run(
            session_id=session.id,
            template_name="authorized-assessment",
            status=WorkflowRunStatus.RUNNING,
            current_stage="safe_validation",
            started_at=datetime(2026, 4, 6, 15, 0, tzinfo=UTC),
            ended_at=None,
            state={
                "goal": "Prefer the stored attack snapshot.",
                "current_stage": "safe_validation",
            },
            last_error=None,
        )

        goal_node = graph_repository.create_node(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.ATTACK,
            node_type="goal",
            label="Prefer the stored attack snapshot.",
            payload={
                "attack_id": f"goal:{run.id}",
                "goal": "Prefer the stored attack snapshot.",
                "source_graphs": ["workflow"],
            },
            stable_key="attack-goal",
        )
        action_node = graph_repository.create_node(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.ATTACK,
            node_type="action",
            label="Noise action",
            payload={
                "attack_id": "attack:snapshot-noise-action",
                "status": "completed",
                "source_graphs": ["task"],
            },
            stable_key="attack-noise-action",
        )
        observation_node = graph_repository.create_node(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.ATTACK,
            node_type="observation",
            label="Noise observation",
            payload={
                "attack_id": "trace:snapshot-noise-observation",
                "status": "completed",
                "source_graphs": ["workflow"],
            },
            stable_key="attack-noise-observation",
        )
        graph_repository.create_node(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.ATTACK,
            node_type="outcome",
            label="Workflow running",
            payload={
                "attack_id": f"outcome:{run.id}",
                "status": "running",
                "source_graphs": ["workflow"],
            },
            stable_key="attack-outcome",
        )
        graph_repository.create_edge(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.ATTACK,
            source_node_id=goal_node.id,
            target_node_id=action_node.id,
            relation="attempts",
            payload={"source_graphs": ["workflow"]},
            stable_key="attack-goal-action",
        )
        graph_repository.create_edge(
            session_id=session.id,
            workflow_run_id=run.id,
            graph_type=GraphType.ATTACK,
            source_node_id=action_node.id,
            target_node_id=observation_node.id,
            relation="discovers",
            payload={"source_graphs": ["workflow"]},
            stable_key="attack-action-observation",
        )
        graph = GraphService(db_session, settings=Settings()).get_graph(
            session_id=session.id,
            graph_type=GraphType.ATTACK,
        )

        node_ids = {node.id for node in graph.nodes}
        assert graph.workflow_run_id == run.id
        assert node_ids == {f"goal:{run.id}", f"outcome:{run.id}"}
        assert all(edge.source in node_ids and edge.target in node_ids for edge in graph.edges)


def _create_conversation_fallback_messages(
    *,
    repository: SessionRepository,
    session_id: str,
    branch_id: str,
) -> None:
    session = repository.get_session(session_id)
    assert session is not None
    user_message = repository.create_message(
        session=session,
        role=MessageRole.USER,
        content="帮我分析登录流程里可能的认证绕过点",
        attachments=[],
        branch_id=branch_id,
        status=MessageStatus.COMPLETED,
        sequence=1,
        turn_index=1,
    )
    trace_payload: dict[str, object] = {
        "trace": [
            {
                "state": "assistant.summary",
                "summary": "<think>优先怀疑 token 校验顺序</think>",
            }
        ]
    }
    assistant_message = repository.create_message(
        session=session,
        role=MessageRole.ASSISTANT,
        content="<think>优先怀疑 token 校验顺序</think>先检查中间件执行顺序。",
        attachments=[],
        branch_id=branch_id,
        status=MessageStatus.COMPLETED,
        sequence=2,
        turn_index=1,
        metadata_json=trace_payload,
        assistant_transcript_json=assistant_transcript_to_storage(
            [
                AssistantTranscriptSegment(
                    id="reasoning-1",
                    sequence=1,
                    kind=AssistantTranscriptSegmentKind.REASONING,
                    status="completed",
                    title="思路进展",
                    text="<think>优先怀疑 token 校验顺序</think>",
                    recorded_at=datetime(2026, 4, 5, 10, 0, tzinfo=UTC),
                    updated_at=datetime(2026, 4, 5, 10, 0, tzinfo=UTC),
                    metadata={"state": "summary.updated"},
                ),
                AssistantTranscriptSegment(
                    id="output-1",
                    sequence=2,
                    kind=AssistantTranscriptSegmentKind.OUTPUT,
                    status="completed",
                    title="正文输出",
                    text="先检查中间件执行顺序。",
                    recorded_at=datetime(2026, 4, 5, 10, 1, tzinfo=UTC),
                    updated_at=datetime(2026, 4, 5, 10, 1, tzinfo=UTC),
                    metadata={},
                ),
            ]
        ),
    )
    generation = repository.create_generation(
        session_id=session.id,
        branch_id=branch_id,
        assistant_message_id=assistant_message.id,
        user_message_id=user_message.id,
        reasoning_summary="<think>优先怀疑 token 校验顺序</think>",
        metadata_json={"source": "chat"},
    )
    repository.update_generation(generation, status=GenerationStatus.COMPLETED)
    repository.update_message(
        assistant_message,
        generation_id=generation.id,
        metadata_json=trace_payload,
    )


def _db_session() -> DBSession:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return DBSession(engine)
