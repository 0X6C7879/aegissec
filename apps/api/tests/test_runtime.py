from typing import Any

from docker.errors import DockerException
from fastapi.testclient import TestClient

from app.db.models import ExecutionStatus
from app.main import app
from app.services.runtime import get_runtime_backend
from tests.utils import api_data


def test_runtime_start_execute_status_and_stop(
    client: TestClient,
    runtime_backend: Any,
) -> None:
    initial_health_response = client.get("/api/runtime/health")

    assert initial_health_response.status_code == 200
    initial_health_payload = api_data(initial_health_response)
    assert initial_health_payload["status"] == "degraded"
    assert initial_health_payload["runtime_status"] == "missing"

    initial_status_response = client.get("/api/runtime/status")

    assert initial_status_response.status_code == 200
    initial_status_payload = api_data(initial_status_response)
    assert initial_status_payload["runtime"]["status"] == "missing"
    assert initial_status_payload["recent_runs"] == []
    assert initial_status_payload["recent_artifacts"] == []

    session_response = client.post("/api/sessions", json={"title": "Runtime Session"})
    session_id = api_data(session_response)["id"]

    start_response = client.post("/api/runtime/start")

    assert start_response.status_code == 200
    start_payload = api_data(start_response)
    assert start_payload["status"] == "running"
    assert start_payload["container_name"] == "aegissec-kali-runtime"

    runtime_health_response = client.get("/api/runtime/health")

    assert runtime_health_response.status_code == 200
    runtime_health_payload = api_data(runtime_health_response)
    assert runtime_health_payload["status"] == "ok"
    assert runtime_health_payload["runtime_status"] == "running"

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
    execute_payload = api_data(execute_response)
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
    status_payload = api_data(status_response)
    assert status_payload["runtime"]["status"] == "running"
    assert len(status_payload["recent_runs"]) == 1
    assert status_payload["recent_runs"][0]["id"] == execute_payload["id"]
    assert status_payload["recent_artifacts"][0]["relative_path"] == "reports/result.txt"

    stop_response = client.post("/api/runtime/stop")

    assert stop_response.status_code == 200
    assert api_data(stop_response)["status"] == "stopped"


def test_runtime_execute_uses_1200s_default_timeout_when_not_specified(
    client: TestClient,
) -> None:
    execute_response = client.post(
        "/api/runtime/execute",
        json={
            "command": "printf 'default timeout'",
            "artifact_paths": [],
        },
    )

    assert execute_response.status_code == 200
    execute_payload = api_data(execute_response)
    assert execute_payload["requested_timeout_seconds"] == 90


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
    failed_payload = api_data(failed_response)
    assert failed_payload["status"] == "failed"
    assert failed_payload["exit_code"] == 2
    assert failed_payload["stderr"] == "command failed"

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
    timeout_payload = api_data(timeout_response)
    assert timeout_payload["status"] == "timeout"
    assert timeout_payload["exit_code"] == 124
    assert timeout_payload["stdout"] == "partial output"

    status_response = client.get("/api/runtime/status")

    assert status_response.status_code == 200
    assert [run["status"] for run in api_data(status_response)["recent_runs"]] == [
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


def test_runtime_execute_remains_on_one_shot_backend_seam(
    client: TestClient,
    runtime_backend: Any,
) -> None:
    terminal_backend = app.state.terminal_backend

    def fail_if_called(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError(
            "interactive terminal backend must not be used by /api/runtime/execute"
        )

    terminal_backend.open_terminal = fail_if_called

    runtime_backend.queue_result(
        status=ExecutionStatus.SUCCESS,
        exit_code=0,
        stdout="one-shot ok",
        stderr="",
    )
    response = client.post(
        "/api/runtime/execute",
        json={
            "command": "printf 'one-shot ok'",
            "timeout_seconds": 10,
            "artifact_paths": [],
        },
    )

    assert response.status_code == 200
    assert api_data(response)["stdout"] == "one-shot ok"


def test_runtime_status_degrades_when_docker_is_unavailable(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    def _raise_docker_exception() -> None:
        raise DockerException("npipe missing")

    monkeypatch.setattr("app.services.runtime.docker.from_env", _raise_docker_exception)

    original_override = app.dependency_overrides[get_runtime_backend]
    app.dependency_overrides[get_runtime_backend] = lambda: get_runtime_backend(app.state.settings)
    try:
        status_response = client.get("/api/runtime/status")

        assert status_response.status_code == 200
        status_payload = api_data(status_response)
        assert status_payload["runtime"]["status"] == "missing"
        assert status_payload["recent_runs"] == []
        assert status_payload["recent_artifacts"] == []

        health_response = client.get("/api/runtime/health")

        assert health_response.status_code == 200
        health_payload = api_data(health_response)
        assert health_payload["status"] == "degraded"
        assert health_payload["runtime_status"] == "missing"

        start_response = client.post("/api/runtime/start")

        assert start_response.status_code == 503
        assert start_response.json()["detail"] == (
            "Docker is not available. Start Docker Desktop or the daemon."
        )
    finally:
        app.dependency_overrides[get_runtime_backend] = original_override


def test_runtime_artifact_index_supports_search_and_pagination(
    client: TestClient,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Artifact Index Session"})
    session_id = api_data(session_response)["id"]

    first_execute = client.post(
        "/api/runtime/execute",
        json={
            "session_id": session_id,
            "command": "printf 'one' > reports/alpha.txt",
            "artifact_paths": ["reports/alpha.txt"],
        },
    )
    second_execute = client.post(
        "/api/runtime/execute",
        json={
            "session_id": session_id,
            "command": "printf 'two' > reports/beta.txt",
            "artifact_paths": ["reports/beta.txt"],
        },
    )

    assert first_execute.status_code == 200
    assert second_execute.status_code == 200

    artifacts_response = client.get(
        "/api/runtime/artifacts",
        params={"session_id": session_id, "q": "beta", "page": 1, "page_size": 1},
    )

    assert artifacts_response.status_code == 200
    payload = artifacts_response.json()
    assert payload["meta"]["pagination"] == {"page": 1, "page_size": 1, "total": 1}
    assert api_data(artifacts_response)[0]["relative_path"] == "reports/beta.txt"


def test_runtime_execute_enforces_session_runtime_policy(
    client: TestClient,
) -> None:
    session_response = client.post(
        "/api/sessions",
        json={
            "title": "Policy Session",
            "runtime_policy_json": {
                "allow_network": False,
                "allow_write": False,
                "max_execution_seconds": 3,
                "max_command_length": 12,
            },
        },
    )
    session_id = api_data(session_response)["id"]

    too_long = client.post(
        "/api/runtime/execute",
        json={
            "session_id": session_id,
            "command": "echo this-is-too-long",
            "timeout_seconds": 1,
            "artifact_paths": [],
        },
    )
    assert too_long.status_code == 400
    assert "max_command_length" in too_long.json()["detail"]

    network_blocked = client.post(
        "/api/runtime/execute",
        json={
            "session_id": session_id,
            "command": "curl x",
            "timeout_seconds": 1,
            "artifact_paths": [],
        },
    )
    assert network_blocked.status_code == 400
    assert network_blocked.json()["detail"] == "Runtime policy blocks network-capable commands."

    write_blocked = client.post(
        "/api/runtime/execute",
        json={
            "session_id": session_id,
            "command": "touch a",
            "timeout_seconds": 1,
            "artifact_paths": [],
        },
    )
    assert write_blocked.status_code == 400
    assert write_blocked.json()["detail"] == "Runtime policy blocks write-capable commands."

    timeout_blocked = client.post(
        "/api/runtime/execute",
        json={
            "session_id": session_id,
            "command": "pwd",
            "timeout_seconds": 10,
            "artifact_paths": [],
        },
    )
    assert timeout_blocked.status_code == 400
    assert "max_execution_seconds" in timeout_blocked.json()["detail"]


def test_runtime_upload_download_and_cleanup_artifacts(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Upload Session"})
    session_id = api_data(session_response)["id"]

    upload_response = client.post(
        "/api/runtime/upload",
        files={"file": ("report.txt", b"uploaded-data", "text/plain")},
        data={"path": "uploads/report.txt", "session_id": session_id, "overwrite": "true"},
    )
    assert upload_response.status_code == 200
    upload_payload = api_data(upload_response)
    assert upload_payload["status"] == "success"
    assert upload_payload["artifacts"][0]["relative_path"] == "uploads/report.txt"

    download_response = client.get("/api/runtime/download", params={"path": "uploads/report.txt"})
    assert download_response.status_code == 200
    assert download_response.content == b"uploaded-data"
    assert "attachment" in download_response.headers["content-disposition"]
    assert "report.txt" in download_response.headers["content-disposition"]

    cleanup_response = client.post("/api/runtime/artifacts/cleanup")
    assert cleanup_response.status_code == 200
    cleanup_payload = api_data(cleanup_response)
    assert "deleted_rows" in cleanup_payload
    assert "deleted_files" in cleanup_payload


def test_runtime_upload_rejects_payload_over_max_size(client: TestClient) -> None:
    app.state.settings.runtime_upload_max_bytes = 8
    upload_response = client.post(
        "/api/runtime/upload",
        files={"file": ("too-large.bin", b"0123456789", "application/octet-stream")},
        data={"path": "uploads/too-large.bin", "overwrite": "true"},
    )
    assert upload_response.status_code == 413
    assert "Upload exceeds configured maximum size" in upload_response.json()["detail"]


def test_runtime_execute_truncates_persisted_output(
    client: TestClient,
    runtime_backend: Any,
) -> None:
    app.state.settings.runtime_output_max_chars = 80
    runtime_backend.queue_result(
        status=ExecutionStatus.SUCCESS,
        exit_code=0,
        stdout="S" * 200,
        stderr="E" * 200,
    )

    execute_response = client.post(
        "/api/runtime/execute",
        json={
            "command": "printf huge-output",
            "artifact_paths": [],
        },
    )

    assert execute_response.status_code == 200
    payload = api_data(execute_response)
    assert len(payload["stdout"]) == 80
    assert len(payload["stderr"]) <= 80
    assert "truncated" in payload["stderr"]

    runs_response = client.get("/api/runtime/runs")
    assert runs_response.status_code == 200
    runs_payload = api_data(runs_response)
    assert len(runs_payload[0]["stdout"]) == 80
    assert len(runs_payload[0]["stderr"]) <= 80


def test_runtime_runs_and_status_include_artifacts_without_n_plus_one_shape_breakage(
    client: TestClient,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Runtime N+1 Session"})
    session_id = api_data(session_response)["id"]

    first_run_response = client.post(
        "/api/runtime/execute",
        json={
            "session_id": session_id,
            "command": "printf 'one' > reports/one.txt",
            "artifact_paths": ["reports/one.txt"],
        },
    )
    second_run_response = client.post(
        "/api/runtime/execute",
        json={
            "session_id": session_id,
            "command": "printf 'two'",
            "artifact_paths": [],
        },
    )

    assert first_run_response.status_code == 200
    assert second_run_response.status_code == 200

    runs_response = client.get(
        "/api/runtime/runs",
        params={"session_id": session_id, "sort_order": "asc", "page_size": 10},
    )
    assert runs_response.status_code == 200
    runs = api_data(runs_response)
    runs_by_command = {run["command"]: run for run in runs}
    assert runs_by_command["printf 'one' > reports/one.txt"]["artifacts"][0]["relative_path"] == (
        "reports/one.txt"
    )
    assert runs_by_command["printf 'two'"]["artifacts"] == []

    status_response = client.get("/api/runtime/status")
    assert status_response.status_code == 200
    status_runs = api_data(status_response)["recent_runs"]
    assert all("artifacts" in run for run in status_runs)


def test_runtime_can_clear_recent_runs(client: TestClient) -> None:
    first_execute = client.post(
        "/api/runtime/execute",
        json={
            "command": "printf 'one'",
            "timeout_seconds": 10,
            "artifact_paths": [],
        },
    )
    second_execute = client.post(
        "/api/runtime/execute",
        json={
            "command": "printf 'two'",
            "timeout_seconds": 10,
            "artifact_paths": [],
        },
    )

    assert first_execute.status_code == 200
    assert second_execute.status_code == 200

    clear_response = client.post("/api/runtime/runs/clear")
    assert clear_response.status_code == 200
    clear_payload = api_data(clear_response)
    assert clear_payload["deleted_runs"] == 2
    assert clear_payload["deleted_artifacts"] == 0

    status_response = client.get("/api/runtime/status")
    assert status_response.status_code == 200
    assert api_data(status_response)["recent_runs"] == []

    runs_response = client.get("/api/runtime/runs")
    assert runs_response.status_code == 200
    assert api_data(runs_response) == []


def test_runtime_profiles_and_session_profile_resolution(client: TestClient) -> None:
    profiles_response = client.get("/api/runtime/profiles")
    assert profiles_response.status_code == 200
    profiles = api_data(profiles_response)
    assert profiles[0]["name"] == "default"
    assert profiles[0]["policy"]["allow_network"] is True

    session_response = client.post(
        "/api/sessions",
        json={
            "title": "Profile Session",
            "runtime_profile_name": "default",
            "runtime_policy_json": {"allow_network": False},
        },
    )
    assert session_response.status_code == 201
    session_id = api_data(session_response)["id"]

    blocked_response = client.post(
        "/api/runtime/execute",
        json={"session_id": session_id, "command": "curl x", "artifact_paths": []},
    )
    assert blocked_response.status_code == 400
    assert blocked_response.json()["detail"] == "Runtime policy blocks network-capable commands."
