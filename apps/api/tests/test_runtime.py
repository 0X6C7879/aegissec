from typing import Any

from fastapi.testclient import TestClient

from app.db.models import ExecutionStatus


def test_runtime_start_execute_status_and_stop(
    client: TestClient,
    runtime_backend: Any,
) -> None:
    initial_status_response = client.get("/api/runtime/status")

    assert initial_status_response.status_code == 200
    assert initial_status_response.json()["runtime"]["status"] == "missing"
    assert initial_status_response.json()["recent_runs"] == []
    assert initial_status_response.json()["recent_artifacts"] == []

    session_response = client.post("/api/sessions", json={"title": "Runtime Session"})
    session_id = session_response.json()["id"]

    start_response = client.post("/api/runtime/start")

    assert start_response.status_code == 200
    assert start_response.json()["status"] == "running"
    assert start_response.json()["container_name"] == "aegissec-kali-runtime"

    runtime_backend.queue_result(
        status=ExecutionStatus.SUCCESS,
        exit_code=0,
        stdout="analysis complete",
        stderr="",
    )
    execute_response = client.post(
        "/api/runtime/execute",
        json={
            "session_id": session_id,
            "command": "printf 'analysis complete' > reports/result.txt",
            "timeout_seconds": 30,
            "artifact_paths": ["reports/result.txt"],
        },
    )

    assert execute_response.status_code == 200
    execute_payload = execute_response.json()
    assert execute_payload["session_id"] == session_id
    assert execute_payload["status"] == "success"
    assert execute_payload["exit_code"] == 0
    assert execute_payload["stdout"] == "analysis complete"
    assert execute_payload["stderr"] == ""
    assert execute_payload["artifacts"] == [
        {
            "id": execute_payload["artifacts"][0]["id"],
            "run_id": execute_payload["id"],
            "relative_path": "reports/result.txt",
            "host_path": execute_payload["artifacts"][0]["host_path"],
            "container_path": "/workspace/reports/result.txt",
            "created_at": execute_payload["artifacts"][0]["created_at"],
        }
    ]

    status_response = client.get("/api/runtime/status")

    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["runtime"]["status"] == "running"
    assert len(status_payload["recent_runs"]) == 1
    assert status_payload["recent_runs"][0]["id"] == execute_payload["id"]
    assert status_payload["recent_artifacts"][0]["relative_path"] == "reports/result.txt"

    stop_response = client.post("/api/runtime/stop")

    assert stop_response.status_code == 200
    assert stop_response.json()["status"] == "stopped"


def test_runtime_execute_tracks_failed_and_timeout_runs(
    client: TestClient,
    runtime_backend: Any,
) -> None:
    runtime_backend.queue_result(
        status=ExecutionStatus.FAILED,
        exit_code=2,
        stdout="",
        stderr="command failed",
    )
    failed_response = client.post(
        "/api/runtime/execute",
        json={
            "command": "false",
            "timeout_seconds": 10,
            "artifact_paths": [],
        },
    )

    assert failed_response.status_code == 200
    assert failed_response.json()["status"] == "failed"
    assert failed_response.json()["exit_code"] == 2
    assert failed_response.json()["stderr"] == "command failed"

    runtime_backend.queue_result(
        status=ExecutionStatus.TIMEOUT,
        exit_code=124,
        stdout="partial output",
        stderr="Command timed out after 5 seconds.",
    )
    timeout_response = client.post(
        "/api/runtime/execute",
        json={
            "command": "sleep 60",
            "timeout_seconds": 5,
            "artifact_paths": [],
        },
    )

    assert timeout_response.status_code == 200
    assert timeout_response.json()["status"] == "timeout"
    assert timeout_response.json()["exit_code"] == 124
    assert timeout_response.json()["stdout"] == "partial output"

    status_response = client.get("/api/runtime/status")

    assert status_response.status_code == 200
    assert [run["status"] for run in status_response.json()["recent_runs"]] == [
        "timeout",
        "failed",
    ]


def test_runtime_execute_rejects_unknown_session_and_invalid_artifact_path(
    client: TestClient,
) -> None:
    unknown_session_response = client.post(
        "/api/runtime/execute",
        json={
            "session_id": "missing-session",
            "command": "pwd",
            "artifact_paths": [],
        },
    )

    assert unknown_session_response.status_code == 404
    assert unknown_session_response.json()["detail"] == "Session not found"

    invalid_artifact_response = client.post(
        "/api/runtime/execute",
        json={
            "command": "pwd",
            "artifact_paths": ["../outside.txt"],
        },
    )

    assert invalid_artifact_response.status_code == 400
    assert invalid_artifact_response.json()["detail"] == (
        "Artifact paths must not contain traversal segments."
    )
