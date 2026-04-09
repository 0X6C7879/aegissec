from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession

from app.db.models import GraphType, TaskNodeStatus, TaskNodeType, WorkflowRunStatus
from app.db.repositories import GraphRepository, WorkflowRepository
from app.main import app
from app.services.chat_runtime import (
    GenerationCallbacks,
    ToolCallRequest,
    ToolExecutor,
    get_chat_runtime,
)
from tests.utils import api_data


def _create_session(client: TestClient) -> str:
    response = client.post("/api/sessions", json={})
    assert response.status_code == 201
    return cast(str, api_data(response)["id"])


def _start_workflow(client: TestClient, session_id: str) -> dict[str, object]:
    del client
    with DBSession(app.state.database_engine) as db_session:
        repository = WorkflowRepository(db_session)
        run = repository.create_run(
            session_id=session_id,
            template_name="authorized-assessment",
            status=WorkflowRunStatus.RUNNING,
            current_stage="scope_guard",
            started_at=datetime.now(UTC),
            ended_at=None,
            state={"current_stage": "scope_guard"},
            last_error=None,
        )
        scope_stage = repository.create_task_node(
            workflow_run_id=run.id,
            name="scope_guard",
            node_type=TaskNodeType.STAGE,
            status=TaskNodeStatus.IN_PROGRESS,
            sequence=1,
            parent_id=None,
            metadata={"title": "范围确认", "stage_key": "scope_guard"},
        )
        repository.create_task_node(
            workflow_run_id=run.id,
            name="scope_guard.confirm_scope",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.READY,
            sequence=2,
            parent_id=scope_stage.id,
            metadata={
                "title": "确认范围与约束",
                "stage_key": "scope_guard",
                "depends_on_task_ids": [scope_stage.id],
            },
        )
        return cast(dict[str, object], {"id": run.id, "current_stage": run.current_stage})


def _advance_workflow(run_id: str) -> dict[str, object]:
    with DBSession(app.state.database_engine) as db_session:
        workflow_repository = WorkflowRepository(db_session)
        graph_repository = GraphRepository(db_session)
        run = workflow_repository.get_run(run_id)
        assert run is not None
        run = workflow_repository.update_run(
            run,
            current_stage="safe_validation",
            state={"current_stage": "safe_validation"},
        )
        graph_repository.create_node(
            session_id=run.session_id,
            workflow_run_id=run.id,
            graph_type=GraphType.EVIDENCE,
            node_type="evidence",
            label="Collected evidence",
            payload={"summary": "evidence summary", "confidence": "high"},
            stable_key="evidence-1",
        )
        evidence_node = graph_repository.list_nodes(
            run.session_id,
            workflow_run_id=run.id,
            graph_type=GraphType.EVIDENCE,
        )[0]
        attack_node = graph_repository.create_node(
            session_id=run.session_id,
            workflow_run_id=run.id,
            graph_type=GraphType.ATTACK,
            node_type="observation",
            label="Observed output",
            payload={"summary": "observation", "confidence": "high"},
            stable_key="attack-observation-1",
        )
        graph_repository.create_edge(
            session_id=run.session_id,
            workflow_run_id=run.id,
            graph_type=GraphType.ATTACK,
            source_node_id=evidence_node.id,
            target_node_id=attack_node.id,
            relation="confirms",
            payload={},
            stable_key="attack-edge-1",
        )
        return cast(dict[str, object], {"id": run.id, "current_stage": run.current_stage})


def test_task_graph_route_returns_persisted_dag_graph(client: TestClient) -> None:
    session_id = _create_session(client)
    workflow = _start_workflow(client, session_id)

    response = client.get(f"/api/sessions/{session_id}/graphs/task")

    assert response.status_code == 200
    payload = api_data(response)
    assert payload["graph_type"] == "task"
    assert payload["workflow_run_id"] == workflow["id"]
    assert payload["current_stage"] == "scope_guard"
    assert len(payload["nodes"]) == 2
    current_nodes = [node for node in payload["nodes"] if node["data"].get("current") is True]
    assert len(current_nodes) == 1
    assert current_nodes[0]["label"] == "范围确认"
    assert {node["label"] for node in payload["nodes"]} == {"范围确认", "确认范围与约束"}
    assert len(payload["edges"]) == 1
    assert all(edge["relation"] == "depends_on" for edge in payload["edges"])


def test_causal_graph_route_returns_empty_graph_for_new_run(client: TestClient) -> None:
    session_id = _create_session(client)
    workflow = _start_workflow(client, session_id)

    response = client.get(f"/api/sessions/{session_id}/graphs/causal")

    assert response.status_code == 200
    payload = api_data(response)
    assert payload["graph_type"] == "causal"
    assert payload["workflow_run_id"] == workflow["id"]
    assert payload["current_stage"] == "scope_guard"
    assert payload["nodes"] == []
    assert payload["edges"] == []


def test_evidence_graph_route_returns_empty_graph_for_new_run(client: TestClient) -> None:
    session_id = _create_session(client)
    workflow = _start_workflow(client, session_id)

    response = client.get(f"/api/sessions/{session_id}/graphs/evidence")

    assert response.status_code == 200
    payload = api_data(response)
    assert payload["graph_type"] == "evidence"
    assert payload["workflow_run_id"] == workflow["id"]
    assert payload["nodes"] == []
    assert payload["edges"] == []


def test_attack_graph_route_returns_unified_attack_path_for_new_run(client: TestClient) -> None:
    session_id = _create_session(client)
    workflow = _start_workflow(client, session_id)

    response = client.get(f"/api/sessions/{session_id}/graphs/attack")

    assert response.status_code == 200
    payload = api_data(response)
    assert payload["graph_type"] == "attack"
    assert payload["workflow_run_id"] == workflow["id"]
    assert payload["current_stage"] == "scope_guard"
    assert payload["session_id"] == session_id
    assert payload["nodes"]
    assert any(node["node_type"] == "outcome" for node in payload["nodes"])

    run_response = client.get(f"/api/workflows/{cast(str, workflow['id'])}/graphs/attack")

    assert run_response.status_code == 200
    run_payload = api_data(run_response)
    assert run_payload["workflow_run_id"] == workflow["id"]
    assert run_payload["graph_type"] == "attack"
    assert run_payload["nodes"]


def test_graph_routes_return_404_for_missing_session(client: TestClient) -> None:
    response = client.get("/api/sessions/missing-session/graphs/task")

    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found"


def test_session_scoped_graph_routes_return_empty_graphs_without_workflow_run(
    client: TestClient,
) -> None:
    session_id = _create_session(client)

    for graph_type in ("task", "causal", "evidence"):
        response = client.get(f"/api/sessions/{session_id}/graphs/{graph_type}")

        assert response.status_code == 200
        payload = api_data(response)
        assert payload["session_id"] == session_id
        assert payload["workflow_run_id"] == ""
        assert payload["graph_type"] == graph_type
        assert payload["current_stage"] is None
        assert payload["nodes"] == []
        assert payload["edges"] == []


def test_attack_graph_route_uses_conversation_fallback_without_workflow_run(
    client: TestClient,
) -> None:
    class ConversationFallbackChatRuntime:
        async def generate_reply(
            self,
            content: str,
            attachments: list[object],
            conversation_messages: list[object] | None = None,
            available_skills: list[object] | None = None,
            skill_context_prompt: str | None = None,
            execute_tool: ToolExecutor | None = None,
            callbacks: GenerationCallbacks | None = None,
        ) -> str:
            del content, attachments, conversation_messages, available_skills, skill_context_prompt
            assert execute_tool is not None
            assert callbacks is not None
            assert callbacks.on_summary is not None
            await callbacks.on_summary("<think>先验证 shell 输出</think>")
            await execute_tool(
                ToolCallRequest(
                    tool_call_id="conversation-shell-1",
                    tool_name="execute_kali_command",
                    arguments={
                        "command": "printf 'fallback' > reports/fallback.txt",
                        "timeout_seconds": 10,
                        "artifact_paths": ["reports/fallback.txt"],
                    },
                )
            )
            return "已拿到 shell 结果，继续分析。"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: ConversationFallbackChatRuntime()

    try:
        session_id = _create_session(client)
        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={
                "content": "请直接运行命令并分析结果",
                "attachments": [],
                "wait_for_completion": True,
            },
        )
        assert chat_response.status_code == 200

        response = client.get(f"/api/sessions/{session_id}/graphs/attack")

        assert response.status_code == 200
        payload = api_data(response)
        assert payload["graph_type"] == "attack"
        assert payload["workflow_run_id"] == ""
        assert payload["current_stage"] is None
        nodes = cast(list[dict[str, object]], payload["nodes"])
        edges = cast(list[dict[str, object]], payload["edges"])
        assert any(node["node_type"] in {"goal", "root"} for node in nodes)
        assert any(node["node_type"] == "action" for node in nodes)
        assert any(node["node_type"] == "outcome" for node in nodes)
        assert any(
            "fallback" in str(cast(dict[str, object], node["data"]).get("command", ""))
            for node in nodes
        )
        assert any(
            edge["relation"]
            in {"attempts", "discovers", "confirms", "validates", "precedes", "enables", "blocks"}
            for edge in edges
        )
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_run_scoped_graph_routes_return_graphs_for_specific_run(client: TestClient) -> None:
    session_id = _create_session(client)
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    response = client.get(f"/api/workflows/{run_id}/graphs/task")

    assert response.status_code == 200
    payload = api_data(response)
    assert payload["workflow_run_id"] == run_id
    assert payload["graph_type"] == "task"

    attack_response = client.get(f"/api/workflows/{run_id}/graphs/attack")

    assert attack_response.status_code == 200
    attack_payload = api_data(attack_response)
    assert attack_payload["workflow_run_id"] == run_id
    assert attack_payload["graph_type"] == "attack"


def test_evidence_graph_includes_summary_and_confidence_after_execution(client: TestClient) -> None:
    session_id = _create_session(client)
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    advance = _advance_workflow(run_id)
    assert advance["id"] == run_id

    response = client.get(f"/api/workflows/{run_id}/graphs/evidence")

    assert response.status_code == 200
    payload = api_data(response)
    assert payload["workflow_run_id"] == run_id
    nodes = cast(list[dict[str, object]], payload["nodes"])
    assert nodes
    assert "summary" in cast(dict[str, object], nodes[0]["data"])
    assert "confidence" in cast(dict[str, object], nodes[0]["data"])


def test_attack_graph_for_run_prunes_noise_only_snapshot_from_default_view(
    client: TestClient,
) -> None:
    session_id = _create_session(client)
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    advance = _advance_workflow(run_id)
    assert advance["id"] == run_id

    response = client.get(f"/api/workflows/{run_id}/graphs/attack")

    assert response.status_code == 200
    payload = api_data(response)
    assert payload["graph_type"] == "attack"
    assert payload["workflow_run_id"] == run_id
    node_ids = {node["id"] for node in cast(list[dict[str, object]], payload["nodes"])}
    observation_nodes = [
        node
        for node in cast(list[dict[str, object]], payload["nodes"])
        if node["node_type"] == "observation"
    ]
    assert not observation_nodes
    for edge in cast(list[dict[str, object]], payload["edges"]):
        assert edge["source"] in node_ids
        assert edge["target"] in node_ids
