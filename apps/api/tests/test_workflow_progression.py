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


def test_advance_workflow_reaches_pre_report_stage(client: TestClient) -> None:
    session_id = _create_session(client)
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    for _ in range(7):
        response = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
        assert response.status_code == 200

    final_detail = client.get(f"/api/workflows/{run_id}")

    assert final_detail.status_code == 200
    payload = final_detail.json()
    assert payload["current_stage"] == "causal_graph_update"
    task_statuses = {task["name"]: task["status"] for task in payload["tasks"]}
    assert task_statuses["causal_graph_update"] == "in_progress"
    assert task_statuses["report_export"] == "pending"
    assert task_statuses["safe_validation"] == "completed"


def test_task_graph_reflects_current_stage_after_progression(client: TestClient) -> None:
    session_id = _create_session(client)
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    response = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
    assert response.status_code == 200

    task_graph = client.get(f"/api/sessions/{session_id}/graphs/task")

    assert task_graph.status_code == 200
    payload = task_graph.json()
    current_nodes = [node for node in payload["nodes"] if node["data"]["current"] is True]
    assert len(current_nodes) == 1
    assert current_nodes[0]["data"]["name"] == "runtime_boot"
    assert current_nodes[0]["label"] == "环境启动"
