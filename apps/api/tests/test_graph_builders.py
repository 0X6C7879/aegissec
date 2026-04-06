from __future__ import annotations

from datetime import UTC, datetime

from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from app.db.models import (
    AssistantTranscriptSegment,
    AssistantTranscriptSegmentKind,
    GenerationStatus,
    GraphType,
    MessageRole,
    MessageStatus,
    Session,
    TaskNodeStatus,
    TaskNodeType,
    WorkflowRunStatus,
    assistant_transcript_to_storage,
)
from app.db.repositories import GraphRepository, SessionRepository, WorkflowRepository
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


def test_attack_graph_builder_prunes_redundant_action_only_branch_from_default_view() -> None:
    with _db_session() as db_session:
        session = _create_session(db_session)
        workflow_repository = WorkflowRepository(db_session)
        run = workflow_repository.create_run(
            session_id=session.id,
            template_name="authorized-assessment",
            status=WorkflowRunStatus.RUNNING,
            current_stage="safe_validation",
            started_at=datetime(2026, 4, 6, 9, 0, tzinfo=UTC),
            ended_at=None,
            state={
                "goal": "Validate the semantic path.",
                "current_stage": "safe_validation",
                "execution_records": [],
            },
            last_error=None,
        )
        action_task = workflow_repository.create_task_node(
            workflow_run_id=run.id,
            name="custom_stage.collect_runtime_context",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.COMPLETED,
            sequence=1,
            parent_id=None,
            metadata={
                "title": "收集运行时上下文",
                "stage_key": "custom_stage",
                "summary": "Generic action node that should collapse.",
            },
        )
        run = workflow_repository.update_run(
            run,
            state={
                "goal": "Validate the semantic path.",
                "current_stage": "safe_validation",
                "execution_records": [
                    {
                        "id": "trace-action-prune",
                        "task_node_id": action_task.id,
                        "status": "completed",
                        "summary": "Observed reachable service.",
                    }
                ],
            },
        )

        graph = AttackGraphBuilder().build(
            run=run,
            tasks=[action_task],
            evidence_nodes=[],
            evidence_edges=[],
            causal_nodes=[],
            causal_edges=[],
        )

        node_ids = {node.id for node in graph.nodes}
        assert action_task.id not in node_ids
        assert "trace-action-prune" not in node_ids
        assert node_ids == {f"goal:{run.id}", f"outcome:{run.id}"}
        assert all(edge.source in node_ids and edge.target in node_ids for edge in graph.edges)


def test_attack_graph_builder_prunes_noise_branch_without_dangling_edges() -> None:
    with _db_session() as db_session:
        session = _create_session(db_session)
        workflow_repository = WorkflowRepository(db_session)
        graph_repository = GraphRepository(db_session)
        run = workflow_repository.create_run(
            session_id=session.id,
            template_name="authorized-assessment",
            status=WorkflowRunStatus.DONE,
            current_stage="safe_validation",
            started_at=datetime(2026, 4, 6, 10, 0, tzinfo=UTC),
            ended_at=datetime(2026, 4, 6, 10, 30, tzinfo=UTC),
            state={
                "goal": "Keep the semantic attack path readable.",
                "current_stage": "safe_validation",
                "seed_message_id": "message-seed-1",
                "execution_records": [],
                "hypothesis_updates": [],
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
            status=TaskNodeStatus.COMPLETED,
            sequence=2,
            parent_id=None,
            metadata={
                "title": "低风险验证执行",
                "stage_key": "safe_validation",
                "summary": "Validated reachable path.",
                "depends_on_task_ids": [collect.id],
            },
        )
        noise_action = workflow_repository.create_task_node(
            workflow_run_id=run.id,
            name="custom_stage.collect_runtime_context",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.COMPLETED,
            sequence=3,
            parent_id=None,
            metadata={
                "title": "收集运行时上下文",
                "stage_key": "custom_stage",
                "summary": "Generic action node that should collapse.",
            },
        )
        run = workflow_repository.update_run(
            run,
            state={
                "goal": "Keep the semantic attack path readable.",
                "current_stage": "safe_validation",
                "seed_message_id": "message-seed-1",
                "execution_records": [
                    {
                        "id": "trace-keep",
                        "task_node_id": collect.id,
                        "status": "completed",
                        "summary": "Observed exposed admin surface.",
                    },
                    {
                        "id": "trace-drop",
                        "task_node_id": noise_action.id,
                        "status": "completed",
                        "summary": "Observed unrelated banner text.",
                    },
                ],
                "hypothesis_updates": [
                    {
                        "task": noise_action.name,
                        "summary": "Unfocused hypothesis branch.",
                    }
                ],
            },
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
                "trace_id": "trace-keep",
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
            tasks=[collect, validate, noise_action],
            evidence_nodes=[],
            evidence_edges=[],
            causal_nodes=[finding_node, impact_node],
            causal_edges=graph_repository.list_edges(
                session.id,
                workflow_run_id=run.id,
                graph_type=GraphType.CAUSAL,
            ),
        )

        node_ids = {node.id for node in graph.nodes}
        relations = {(edge.source, edge.relation, edge.target) for edge in graph.edges}

        assert noise_action.id not in node_ids
        assert "trace-drop" not in node_ids
        assert f"hypothesis:{noise_action.name}" not in node_ids
        assert "trace-keep" in node_ids
        assert "finding-auth" in node_ids
        assert f"outcome:{run.id}" in node_ids
        assert (collect.id, "enables", validate.id) in relations
        assert ("trace-keep", "confirms", "finding-auth") in relations
        assert ("finding-auth", "confirms", f"outcome:{run.id}") in relations
        for edge in graph.edges:
            assert edge.source in node_ids
            assert edge.target in node_ids


def test_attack_graph_builder_keeps_preserved_status_noise_nodes() -> None:
    with _db_session() as db_session:
        session = _create_session(db_session)
        workflow_repository = WorkflowRepository(db_session)
        graph_repository = GraphRepository(db_session)
        run = workflow_repository.create_run(
            session_id=session.id,
            template_name="authorized-assessment",
            status=WorkflowRunStatus.RUNNING,
            current_stage="safe_validation",
            started_at=datetime(2026, 4, 6, 11, 0, tzinfo=UTC),
            ended_at=None,
            state={
                "goal": "Keep blocked and failed nodes visible.",
                "current_stage": "safe_validation",
                "seed_message_id": "message-seed-1",
                "execution_records": [],
                "hypothesis_updates": [],
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
        blocked_action = workflow_repository.create_task_node(
            workflow_run_id=run.id,
            name="custom_stage.collect_runtime_context",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.BLOCKED,
            sequence=3,
            parent_id=None,
            metadata={
                "title": "收集运行时上下文",
                "stage_key": "custom_stage",
                "summary": "Blocked branch that should remain visible.",
            },
        )
        run = workflow_repository.update_run(
            run,
            state={
                "goal": "Keep blocked and failed nodes visible.",
                "current_stage": "safe_validation",
                "seed_message_id": "message-seed-1",
                "execution_records": [
                    {
                        "id": "trace-keep",
                        "task_node_id": collect.id,
                        "status": "completed",
                        "summary": "Observed exposed admin surface.",
                    },
                    {
                        "id": "trace-blocked",
                        "task_node_id": blocked_action.id,
                        "status": "failed",
                        "summary": "Blocked by runtime restriction.",
                    },
                ],
                "hypothesis_updates": [
                    {
                        "task": blocked_action.name,
                        "kind": "Preserved branch",
                        "status": "blocked",
                        "summary": "Need follow-up after approval.",
                    }
                ],
            },
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
                "trace_id": "trace-keep",
                "task": validate.name,
            },
            stable_key="causal-node:finding-auth",
        )

        graph = AttackGraphBuilder().build(
            run=run,
            tasks=[collect, validate, blocked_action],
            evidence_nodes=[],
            evidence_edges=[],
            causal_nodes=[finding_node],
            causal_edges=[],
        )

        node_ids = {node.id for node in graph.nodes}
        relations = {(edge.source, edge.relation, edge.target) for edge in graph.edges}

        assert blocked_action.id in node_ids
        assert "trace-blocked" in node_ids
        assert f"hypothesis:{blocked_action.name}" in node_ids
        assert (blocked_action.id, "blocks", "trace-blocked") in relations
        assert (blocked_action.id, "attempts", f"hypothesis:{blocked_action.name}") in relations
        assert ("trace-keep", "confirms", "finding-auth") in relations


def test_attack_graph_builder_uses_status_specific_outcome_relations() -> None:
    expectations = {
        WorkflowRunStatus.RUNNING: "attempts",
        WorkflowRunStatus.BLOCKED: "blocks",
        WorkflowRunStatus.DONE: "confirms",
    }

    for status, expected_relation in expectations.items():
        with _db_session() as db_session:
            session = _create_session(db_session)
            workflow_repository = WorkflowRepository(db_session)
            graph_repository = GraphRepository(db_session)
            ended_at = (
                datetime(2026, 4, 6, 12, 30, tzinfo=UTC)
                if status is WorkflowRunStatus.DONE
                else None
            )
            run = workflow_repository.create_run(
                session_id=session.id,
                template_name="authorized-assessment",
                status=status,
                current_stage="safe_validation",
                started_at=datetime(2026, 4, 6, 12, 0, tzinfo=UTC),
                ended_at=ended_at,
                state={
                    "goal": f"Check outcome semantics for {status.value}.",
                    "current_stage": "safe_validation",
                    "seed_message_id": "message-seed-1",
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
                },
            )
            validate = workflow_repository.create_task_node(
                workflow_run_id=run.id,
                name="safe_validation.validate_primary_hypothesis",
                node_type=TaskNodeType.TASK,
                status=(
                    TaskNodeStatus.COMPLETED
                    if status is WorkflowRunStatus.DONE
                    else TaskNodeStatus.IN_PROGRESS
                ),
                sequence=2,
                parent_id=None,
                metadata={
                    "title": "低风险验证执行",
                    "stage_key": "safe_validation",
                    "depends_on_task_ids": [collect.id],
                },
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
                    "task": validate.name,
                },
                stable_key="causal-node:finding-auth",
            )

            graph = AttackGraphBuilder().build(
                run=run,
                tasks=[collect, validate],
                evidence_nodes=[],
                evidence_edges=[],
                causal_nodes=[finding_node],
                causal_edges=[],
            )

            relations = {(edge.source, edge.relation, edge.target) for edge in graph.edges}
            assert ("finding-auth", expected_relation, f"outcome:{run.id}") in relations


def test_attack_graph_builder_builds_conversation_fallback_for_pure_chat_and_reasoning() -> None:
    with _db_session() as db_session:
        repository = SessionRepository(db_session)
        session = repository.create_session(
            title="Conversation Graph Session",
            goal="确认登录逻辑中的薄弱点",
        )
        branch = repository.ensure_active_branch(session)
        user_message = repository.create_message(
            session=session,
            role=MessageRole.USER,
            content="帮我分析登录流程里可能的认证绕过点",
            attachments=[],
            branch_id=branch.id,
            status=MessageStatus.COMPLETED,
            sequence=1,
            turn_index=1,
        )
        assistant_message = repository.create_message(
            session=session,
            role=MessageRole.ASSISTANT,
            content="<think>优先怀疑 token 校验顺序</think>先检查中间件执行顺序。",
            attachments=[],
            branch_id=branch.id,
            status=MessageStatus.COMPLETED,
            sequence=2,
            turn_index=1,
            metadata_json={
                "trace": [
                    {
                        "state": "assistant.summary",
                        "summary": "<think>优先怀疑 token 校验顺序</think>",
                    }
                ]
            },
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
            branch_id=branch.id,
            assistant_message_id=assistant_message.id,
            user_message_id=user_message.id,
            reasoning_summary="<think>优先怀疑 token 校验顺序</think>",
            metadata_json={"source": "chat"},
        )
        repository.update_generation(generation, status=GenerationStatus.COMPLETED)
        assistant_message = repository.update_message(
            assistant_message,
            generation_id=generation.id,
            metadata_json={
                "trace": [
                    {
                        "state": "assistant.summary",
                        "summary": "<think>优先怀疑 token 校验顺序</think>",
                    }
                ]
            },
        )

        graph = AttackGraphBuilder().build_from_conversation(
            session=session,
            messages=[user_message, assistant_message],
            generations=[generation],
        )

        node_types = {node.id: node.node_type for node in graph.nodes}
        assert graph.workflow_run_id == ""
        assert node_types
        assert "goal" in node_types.values()
        assert "action" in node_types.values()
        assert "observation" in node_types.values()
        assert "hypothesis" in node_types.values()
        assert "outcome" in node_types.values()
        assert any(
            "token 校验顺序" in node.label for node in graph.nodes if node.node_type == "hypothesis"
        )
        relations = {(edge.source, edge.relation, edge.target) for edge in graph.edges}
        assert any(relation == "attempts" for _, relation, _ in relations)
        assert any(
            relation in {"discovers", "observes", "confirms", "validates"}
            for _, relation, _ in relations
        )


def test_attack_graph_builder_builds_conversation_fallback_for_shell_tool_results() -> None:
    with _db_session() as db_session:
        repository = SessionRepository(db_session)
        session = repository.create_session(title="Shell Graph Session", goal="验证 shell 输出")
        branch = repository.ensure_active_branch(session)
        user_message = repository.create_message(
            session=session,
            role=MessageRole.USER,
            content="运行一条命令收集线索",
            attachments=[],
            branch_id=branch.id,
            status=MessageStatus.COMPLETED,
            sequence=1,
            turn_index=1,
        )
        assistant_message = repository.create_message(
            session=session,
            role=MessageRole.ASSISTANT,
            content="已记录 shell 输出。",
            attachments=[],
            branch_id=branch.id,
            status=MessageStatus.COMPLETED,
            sequence=2,
            turn_index=1,
            assistant_transcript_json=assistant_transcript_to_storage(
                [
                    AssistantTranscriptSegment(
                        id="tool-call-1",
                        sequence=1,
                        kind=AssistantTranscriptSegmentKind.TOOL_CALL,
                        status="completed",
                        title="execute_kali_command",
                        text="printf 'hello'",
                        tool_name="execute_kali_command",
                        tool_call_id="tool-call-1",
                        recorded_at=datetime(2026, 4, 5, 11, 0, tzinfo=UTC),
                        updated_at=datetime(2026, 4, 5, 11, 0, tzinfo=UTC),
                        metadata={"arguments": {"command": "printf 'hello'"}},
                    ),
                    AssistantTranscriptSegment(
                        id="tool-result-1",
                        sequence=2,
                        kind=AssistantTranscriptSegmentKind.TOOL_RESULT,
                        status="completed",
                        title="execute_kali_command",
                        text=None,
                        tool_name="execute_kali_command",
                        tool_call_id="tool-call-1",
                        recorded_at=datetime(2026, 4, 5, 11, 1, tzinfo=UTC),
                        updated_at=datetime(2026, 4, 5, 11, 1, tzinfo=UTC),
                        metadata={
                            "result": {
                                "status": "success",
                                "command": "printf 'hello'",
                                "stdout": "hello",
                                "stderr": "",
                                "exit_code": 0,
                                "artifacts": ["reports/hello.txt"],
                            }
                        },
                    ),
                ]
            ),
        )

        graph = AttackGraphBuilder().build_from_conversation(
            session=session,
            messages=[user_message, assistant_message],
            generations=[],
        )

        assert any(node.node_type == "goal" for node in graph.nodes)
        assert any(
            node.node_type == "exploit" and "printf 'hello'" in node.label for node in graph.nodes
        )
        assert any(
            node.node_type == "observation" and "hello" in str(node.data.get("stdout"))
            for node in graph.nodes
        )
        assert any(node.node_type == "outcome" for node in graph.nodes)
        relations = {(edge.source, edge.relation, edge.target) for edge in graph.edges}
        assert any(relation == "discovers" for _, relation, _ in relations)


def test_attack_graph_builder_classifies_execute_skill_security_tools_as_exploit() -> None:
    with _db_session() as db_session:
        repository = SessionRepository(db_session)
        session = repository.create_session(title="Skill Graph Session", goal="执行 adscan")
        branch = repository.ensure_active_branch(session)
        user_message = repository.create_message(
            session=session,
            role=MessageRole.USER,
            content="自动执行 adscan skill",
            attachments=[],
            branch_id=branch.id,
            status=MessageStatus.COMPLETED,
            sequence=1,
            turn_index=1,
        )
        assistant_message = repository.create_message(
            session=session,
            role=MessageRole.ASSISTANT,
            content="已准备 adscan 技能上下文。",
            attachments=[],
            branch_id=branch.id,
            status=MessageStatus.COMPLETED,
            sequence=2,
            turn_index=1,
            assistant_transcript_json=assistant_transcript_to_storage(
                [
                    AssistantTranscriptSegment(
                        id="skill-call-1",
                        sequence=1,
                        kind=AssistantTranscriptSegmentKind.TOOL_CALL,
                        status="completed",
                        title="execute_skill",
                        text="adscan",
                        tool_name="execute_skill",
                        tool_call_id="skill-call-1",
                        recorded_at=datetime(2026, 4, 5, 12, 5, tzinfo=UTC),
                        updated_at=datetime(2026, 4, 5, 12, 5, tzinfo=UTC),
                        metadata={"arguments": {"skill_name_or_id": "adscan"}},
                    )
                ]
            ),
        )

        graph = AttackGraphBuilder().build_from_conversation(
            session=session,
            messages=[user_message, assistant_message],
            generations=[],
        )

    assert any(node.node_type == "exploit" and "adscan" in node.label for node in graph.nodes)


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
