from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient

from tests.utils import api_data


def _create_session(client: TestClient) -> str:
    response = client.post("/api/sessions", json={"goal": "progress workflow"})
    assert response.status_code == 201
    return cast(str, api_data(response)["id"])


def _start_workflow(client: TestClient, session_id: str) -> dict[str, Any]:
    response = client.post(
        "/api/workflows/authorized-assessment/start",
        json={"session_id": session_id},
    )
    assert response.status_code == 201
    return cast(dict[str, Any], api_data(response))


def test_first_advance_moves_to_next_runnable_stage(client: TestClient) -> None:
    session_id = _create_session(client)
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    response = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})

    assert response.status_code == 200
    payload = cast(dict[str, Any], api_data(response))
    assert payload["status"] == "running"
    assert payload["current_stage"] == "scope_guard"
    state = cast(dict[str, Any], payload["state"])
    batch = cast(dict[str, Any], state["batch"])
    assert batch["contract_version"] == "v1"
    assert batch["status"] == "completed"
    assert len(cast(list[str], batch["executed_task_ids"])) >= 1

    tasks_by_name = {task["name"]: task for task in cast(list[dict[str, Any]], payload["tasks"])}
    assert tasks_by_name["scope_guard"]["status"] == "completed"
    assert tasks_by_name["scope_guard.scope_constraints"]["status"] in {"ready", "completed"}
    assert tasks_by_name["scope_guard.scope_constraints"]["metadata"]["summary"]

    task_graph = client.get(f"/api/sessions/{session_id}/graphs/task")
    task_payload = cast(dict[str, Any], api_data(task_graph))
    current_nodes = [
        node
        for node in cast(list[dict[str, Any]], task_payload["nodes"])
        if node["data"].get("current") is True
    ]
    assert {node["data"]["name"] for node in current_nodes} == {
        "scope_guard",
        "scope_guard.scope_constraints",
    }


def test_approve_true_can_drive_entire_workflow_to_done(client: TestClient) -> None:
    session_id = _create_session(client)
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    final_payload: dict[str, Any] | None = None
    for _ in range(40):
        response = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
        assert response.status_code == 200
        final_payload = cast(dict[str, Any], api_data(response))
        if final_payload["status"] == "done":
            break

    assert final_payload is not None
    assert final_payload["status"] == "done"

    detail = client.get(f"/api/workflows/{run_id}")
    detail_payload = cast(dict[str, Any], api_data(detail))
    assert detail_payload["status"] == "done"
    task_statuses = {
        task["name"]: task["status"] for task in cast(list[dict[str, Any]], detail_payload["tasks"])
    }
    assert all(status == "completed" for status in task_statuses.values())
