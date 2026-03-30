from __future__ import annotations

from typing import cast

from fastapi.testclient import TestClient


def _create_session(client: TestClient) -> str:
    response = client.post("/api/sessions", json={})
    assert response.status_code == 201
    return cast(str, response.json()["id"])


def _start_workflow(client: TestClient, session_id: str) -> dict[str, object]:
    response = client.post(
        "/api/workflows/authorized-assessment/start",
        json={"session_id": session_id},
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def test_task_graph_route_returns_ordered_stage_graph(client: TestClient) -> None:
    session_id = _create_session(client)
    workflow = _start_workflow(client, session_id)

    response = client.get(f"/api/sessions/{session_id}/graphs/task")

    assert response.status_code == 200
    payload = response.json()
    assert payload["graph_type"] == "task"
    assert payload["workflow_run_id"] == workflow["id"]
    assert payload["current_stage"] == "scope_guard"
    assert len(payload["nodes"]) == 9
    assert payload["nodes"][0]["label"] == "范围确认"
    assert payload["nodes"][0]["data"]["current"] is True
    assert len(payload["edges"]) == 8
    assert payload["edges"][0]["relation"] == "precedes"


def test_causal_graph_route_returns_empty_graph_for_new_run(client: TestClient) -> None:
    session_id = _create_session(client)
    workflow = _start_workflow(client, session_id)

    response = client.get(f"/api/sessions/{session_id}/graphs/causal")

    assert response.status_code == 200
    payload = response.json()
    assert payload["graph_type"] == "causal"
    assert payload["workflow_run_id"] == workflow["id"]
    assert payload["current_stage"] == "scope_guard"
    assert payload["nodes"] == []
    assert payload["edges"] == []


def test_graph_routes_return_404_for_missing_session(client: TestClient) -> None:
    response = client.get("/api/sessions/missing-session/graphs/task")

    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found"
