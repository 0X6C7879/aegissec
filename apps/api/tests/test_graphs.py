from __future__ import annotations

from typing import cast

from fastapi.testclient import TestClient

from tests.utils import api_data


def _create_session(client: TestClient) -> str:
    response = client.post("/api/sessions", json={})
    assert response.status_code == 201
    return cast(str, api_data(response)["id"])


def _start_workflow(client: TestClient, session_id: str) -> dict[str, object]:
    response = client.post(
        "/api/workflows/authorized-assessment/start",
        json={"session_id": session_id},
    )
    assert response.status_code == 201
    return cast(dict[str, object], api_data(response))


def test_task_graph_route_returns_persisted_dag_graph(client: TestClient) -> None:
    session_id = _create_session(client)
    workflow = _start_workflow(client, session_id)

    response = client.get(f"/api/sessions/{session_id}/graphs/task")

    assert response.status_code == 200
    payload = api_data(response)
    assert payload["graph_type"] == "task"
    assert payload["workflow_run_id"] == workflow["id"]
    assert payload["current_stage"] == "scope_guard"
    assert len(payload["nodes"]) >= 19
    current_nodes = [node for node in payload["nodes"] if node["data"].get("current") is True]
    assert len(current_nodes) == 2
    assert {node["label"] for node in current_nodes} == {"范围确认", "确认范围与约束"}
    assert len(payload["edges"]) >= 18
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
    nodes = cast(list[dict[str, object]], payload["nodes"])
    edges = cast(list[dict[str, object]], payload["edges"])
    assert any(node["node_type"] == "goal" for node in nodes)
    assert any(node["label"] == "攻击面清点" for node in nodes)
    assert any(
        edge["relation"] in {"attempts", "enables", "branches_from", "blocks"} for edge in edges
    )


def test_graph_routes_return_404_for_missing_session(client: TestClient) -> None:
    response = client.get("/api/sessions/missing-session/graphs/task")

    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found"


def test_session_scoped_graph_routes_return_empty_graphs_without_workflow_run(
    client: TestClient,
) -> None:
    session_id = _create_session(client)

    for graph_type in ("task", "causal", "evidence", "attack"):
        response = client.get(f"/api/sessions/{session_id}/graphs/{graph_type}")

        assert response.status_code == 200
        payload = api_data(response)
        assert payload["session_id"] == session_id
        assert payload["workflow_run_id"] == ""
        assert payload["graph_type"] == graph_type
        assert payload["current_stage"] is None
        assert payload["nodes"] == []
        assert payload["edges"] == []


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

    advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
    assert advance.status_code == 200

    response = client.get(f"/api/workflows/{run_id}/graphs/evidence")

    assert response.status_code == 200
    payload = api_data(response)
    assert payload["workflow_run_id"] == run_id
    nodes = cast(list[dict[str, object]], payload["nodes"])
    assert nodes
    assert "summary" in cast(dict[str, object], nodes[0]["data"])
    assert "confidence" in cast(dict[str, object], nodes[0]["data"])


def test_attack_graph_for_run_includes_observations_after_execution(client: TestClient) -> None:
    session_id = _create_session(client)
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
    assert advance.status_code == 200

    response = client.get(f"/api/workflows/{run_id}/graphs/attack")

    assert response.status_code == 200
    payload = api_data(response)
    assert payload["graph_type"] == "attack"
    observation_nodes = [
        node
        for node in cast(list[dict[str, object]], payload["nodes"])
        if node["node_type"] == "observation"
    ]
    assert observation_nodes
