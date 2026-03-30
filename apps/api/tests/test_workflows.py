from __future__ import annotations

from typing import cast

from fastapi.testclient import TestClient

AUTHORIZED_ASSESSMENT_STAGE_ORDER = [
    "scope_guard",
    "runtime_boot",
    "skill_mcp_sync",
    "context_collect",
    "hypothesis_build",
    "safe_validation",
    "findings_merge",
    "causal_graph_update",
    "report_export",
]


def _create_session(client: TestClient) -> str:
    response = client.post("/api/sessions", json={})
    assert response.status_code == 201
    return cast(str, response.json()["id"])


def test_start_workflow_creates_run_with_deterministic_stage_skeleton(client: TestClient) -> None:
    session_id = _create_session(client)

    response = client.post(
        "/api/workflows/authorized-assessment/start",
        json={"session_id": session_id},
    )

    assert response.status_code == 201
    payload = response.json()

    assert payload["session_id"] == session_id
    assert payload["template_name"] == "authorized-assessment"
    assert payload["status"] == "running"
    assert payload["current_stage"] == AUTHORIZED_ASSESSMENT_STAGE_ORDER[0]
    assert payload["state"]["session_id"] == session_id
    assert payload["state"]["current_stage"] == AUTHORIZED_ASSESSMENT_STAGE_ORDER[0]
    assert payload["state"]["stage_order"] == AUTHORIZED_ASSESSMENT_STAGE_ORDER
    assert payload["state"]["messages"] == []
    assert payload["state"]["skill_snapshot"] == []
    assert payload["state"]["mcp_snapshot"] == []
    assert payload["state"]["findings"] == []
    assert payload["state"]["graph_updates"] == []

    task_names = [task["name"] for task in payload["tasks"]]
    task_statuses = [task["status"] for task in payload["tasks"]]

    assert task_names == AUTHORIZED_ASSESSMENT_STAGE_ORDER
    assert task_statuses == [
        "in_progress",
        *(["pending"] * (len(AUTHORIZED_ASSESSMENT_STAGE_ORDER) - 1)),
    ]
    assert payload["tasks"][0]["node_type"] == "stage"
    assert payload["tasks"][0]["metadata"]["title"] == "范围确认"
    assert payload["tasks"][5]["metadata"]["requires_approval"] is True


def test_get_workflow_detail_returns_persisted_run_and_tasks(client: TestClient) -> None:
    session_id = _create_session(client)
    start_response = client.post(
        "/api/workflows/authorized-assessment/start",
        json={"session_id": session_id},
    )
    run_id = start_response.json()["id"]

    response = client.get(f"/api/workflows/{run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == run_id
    assert payload["session_id"] == session_id
    assert payload["current_stage"] == AUTHORIZED_ASSESSMENT_STAGE_ORDER[0]
    assert [task["name"] for task in payload["tasks"]] == AUTHORIZED_ASSESSMENT_STAGE_ORDER


def test_start_workflow_blocks_prior_active_run_for_same_session(client: TestClient) -> None:
    session_id = _create_session(client)
    first_response = client.post(
        "/api/workflows/authorized-assessment/start",
        json={"session_id": session_id},
    )
    second_response = client.post(
        "/api/workflows/authorized-assessment/start",
        json={"session_id": session_id},
    )

    assert first_response.status_code == 201
    assert second_response.status_code == 201

    first_run_id = first_response.json()["id"]
    second_run_id = second_response.json()["id"]
    assert first_run_id != second_run_id

    first_detail = client.get(f"/api/workflows/{first_run_id}")
    second_detail = client.get(f"/api/workflows/{second_run_id}")

    assert first_detail.status_code == 200
    assert second_detail.status_code == 200
    assert first_detail.json()["status"] == "blocked"
    assert first_detail.json()["ended_at"] is not None
    assert second_detail.json()["status"] == "running"
    assert second_detail.json()["current_stage"] == AUTHORIZED_ASSESSMENT_STAGE_ORDER[0]


def test_start_workflow_rejects_unknown_template_and_missing_session(client: TestClient) -> None:
    missing_session_response = client.post(
        "/api/workflows/authorized-assessment/start",
        json={"session_id": "missing-session"},
    )
    unknown_template_response = client.post(
        "/api/workflows/not-real/start",
        json={"session_id": _create_session(client)},
    )
    missing_run_response = client.get("/api/workflows/missing-run")

    assert missing_session_response.status_code == 404
    assert missing_session_response.json()["detail"] == "Session not found."
    assert unknown_template_response.status_code == 404
    assert unknown_template_response.json()["detail"] == "Workflow template not found."
    assert missing_run_response.status_code == 404
    assert missing_run_response.json()["detail"] == "Workflow run not found."


def test_advance_workflow_requires_approval_for_safe_validation(client: TestClient) -> None:
    session_id = _create_session(client)
    start_response = client.post(
        "/api/workflows/authorized-assessment/start",
        json={"session_id": session_id},
    )
    run_id = cast(str, start_response.json()["id"])

    for _ in range(4):
        response = client.post(f"/api/workflows/{run_id}/advance", json={})
        assert response.status_code == 200

    blocked_response = client.post(f"/api/workflows/{run_id}/advance", json={})

    assert blocked_response.status_code == 409
    assert blocked_response.json()["detail"] == "Approval required."

    detail_response = client.get(f"/api/workflows/{run_id}")
    detail_payload = detail_response.json()
    assert detail_payload["status"] == "needs_approval"
    assert detail_payload["current_stage"] == "safe_validation"
    assert detail_payload["tasks"][5]["status"] == "blocked"

    approved_response = client.post(
        f"/api/workflows/{run_id}/advance",
        json={"approve": True},
    )

    assert approved_response.status_code == 200
    approved_payload = approved_response.json()
    assert approved_payload["status"] == "running"
    assert approved_payload["current_stage"] == "safe_validation"
    assert approved_payload["tasks"][5]["status"] == "in_progress"


def test_advance_workflow_progresses_to_stage_before_report(client: TestClient) -> None:
    session_id = _create_session(client)
    start_response = client.post(
        "/api/workflows/authorized-assessment/start",
        json={"session_id": session_id},
    )
    run_id = cast(str, start_response.json()["id"])

    for step in range(7):
        payload = {"approve": True} if step == 4 else {}
        response = client.post(f"/api/workflows/{run_id}/advance", json=payload)
        assert response.status_code == 200

    detail_response = client.get(f"/api/workflows/{run_id}")
    detail_payload = detail_response.json()

    assert detail_payload["status"] == "running"
    assert detail_payload["current_stage"] == "causal_graph_update"
    assert detail_payload["tasks"][7]["status"] == "in_progress"
    assert detail_payload["tasks"][6]["status"] == "completed"

    graph_response = client.get(f"/api/sessions/{session_id}/graphs/task")
    graph_payload = graph_response.json()
    current_nodes = [node for node in graph_payload["nodes"] if node["data"]["current"] is True]

    assert graph_response.status_code == 200
    assert graph_payload["current_stage"] == "causal_graph_update"
    assert [node["label"] for node in current_nodes] == ["因果图更新"]


def test_start_workflow_publishes_workflow_and_graph_events(client: TestClient) -> None:
    session_id = _create_session(client)

    with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
        response = client.post(
            "/api/workflows/authorized-assessment/start",
            json={"session_id": session_id},
        )

        assert response.status_code == 201
        event_types = [websocket.receive_json()["type"] for _ in range(13)]

    assert event_types[0] == "workflow.run.started"
    assert event_types[1] == "workflow.stage.changed"
    assert event_types.count("workflow.task.updated") == 9
    assert event_types.count("graph.updated") == 2
