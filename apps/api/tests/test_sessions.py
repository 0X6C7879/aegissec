import asyncio
import importlib
import json
import logging
import threading
import time
from collections.abc import Coroutine
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from pytest import MonkeyPatch
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session as DBSession
from starlette.testclient import WebSocketDenialResponse

from app.compat.mcp.service import get_mcp_service
from app.compat.skills.models import SkillScanRoot
from app.compat.skills.service import SkillContentReadError, SkillService
from app.core.events import get_event_broker
from app.db.models import (
    CompatibilityScope,
    CompatibilitySource,
    GenerationStatus,
    MCPCapabilityKind,
    MCPCapabilityRead,
    MCPServerRead,
    MCPServerStatus,
    MCPTransport,
    MessageRole,
    MessageStatus,
    RuntimeTerminalJobStatus,
)
from app.db.repositories import RunLogRepository, SessionRepository, TerminalRepository
from app.db.repositories.terminal_repository import _commit_and_refresh, _flush_pending
from app.db.session import get_websocket_db_session
from app.harness import session_runner as harness_session_runner
from app.harness.session_runner import _publish_auto_compaction_events_if_needed
from app.harness.transcript import (
    hidden_stream_tag_names,
    project_visible_stream_content,
    sanitize_persisted_assistant_text,
)
from app.main import app
from app.services.chat_runtime import (
    AnthropicChatRuntime,
    ChatRuntimeError,
    GenerationCallbacks,
    OpenAICompatibleChatRuntime,
    ToolCallRequest,
    ToolExecutor,
    get_chat_runtime,
)
from app.services.session_generation import get_generation_manager, recover_abandoned_generations
from app.services.terminal_sessions import SessionShellService
from tests.utils import api_data

terminal_runtime = importlib.import_module("app.services.terminal_runtime")

TEST_POLL_INTERVAL_SECONDS = 0.01
TEST_EVENTUAL_TIMEOUT_SECONDS = 1.0


def _yield_control() -> Coroutine[object, object, None]:
    return asyncio.sleep(0)


def test_chat_hidden_stream_tag_names_always_keep_think_visible() -> None:
    assert hidden_stream_tag_names() == {"invoke", "tool_call"}


def test_chat_sanitize_persisted_assistant_text_preserves_full_model_output() -> None:
    content = (
        '<minimax:tool_call><invoke name="agent-browser">{"task":"demo"}'
        "</invoke></minimax:tool_call><think>private</think>最终答复"
    )

    sanitized = sanitize_persisted_assistant_text(content)

    assert sanitized == content


def test_chat_project_visible_stream_content_preserves_full_model_output() -> None:
    raw_streamed_content = (
        "<think>hidden</think>"
        '<minimax:tool_call><invoke name="agent-browser">{"task":"demo"}'
        "</invoke></minimax:tool_call>最终"
    )

    projected = project_visible_stream_content(raw_streamed_content)

    assert projected == raw_streamed_content


def test_session_lifecycle_and_history(client: TestClient) -> None:
    create_response = client.post("/api/sessions", json={"title": "Initial Session"})

    assert create_response.status_code == 201
    created_session = api_data(create_response)
    session_id = created_session["id"]
    assert created_session["title"] == "Initial Session"
    assert created_session["status"] == "idle"
    assert created_session["project_id"] is None
    assert created_session["goal"] is None
    assert created_session["scenario_type"] is None
    assert created_session["current_phase"] is None
    assert created_session["runtime_policy_json"] is None

    list_response = client.get("/api/sessions")

    assert list_response.status_code == 200
    assert [session["id"] for session in api_data(list_response)] == [session_id]

    detail_response = client.get(f"/api/sessions/{session_id}")

    assert detail_response.status_code == 200
    assert api_data(detail_response)["messages"] == []

    rename_response = client.patch(
        f"/api/sessions/{session_id}",
        json={
            "title": "Renamed Session",
            "status": "paused",
            "goal": "Validate attack path.",
            "scenario_type": "web",
            "current_phase": "triage",
            "runtime_policy_json": {"approval": "manual"},
        },
    )

    assert rename_response.status_code == 200
    renamed_payload = api_data(rename_response)
    assert renamed_payload["title"] == "Renamed Session"
    assert renamed_payload["status"] == "paused"
    assert renamed_payload["goal"] == "Validate attack path."
    assert renamed_payload["scenario_type"] == "web"
    assert renamed_payload["current_phase"] == "triage"
    assert renamed_payload["runtime_policy_json"] == {"approval": "manual"}

    pause_response = client.post(f"/api/sessions/{session_id}/pause")

    assert pause_response.status_code == 200
    assert api_data(pause_response)["status"] == "paused"

    resume_response = client.post(f"/api/sessions/{session_id}/resume")

    assert resume_response.status_code == 200
    assert api_data(resume_response)["status"] == "running"

    cancel_response = client.post(f"/api/sessions/{session_id}/cancel")

    assert cancel_response.status_code == 200
    assert api_data(cancel_response)["status"] == "cancelled"

    delete_response = client.delete(f"/api/sessions/{session_id}")

    assert delete_response.status_code == 200
    assert client.get(f"/api/sessions/{session_id}").status_code == 404
    assert api_data(client.get("/api/sessions")) == []

    restore_response = client.post(f"/api/sessions/{session_id}/restore")

    assert restore_response.status_code == 200
    restored_payload = api_data(restore_response)
    assert restored_payload["id"] == session_id
    assert restored_payload["deleted_at"] is None

    restored_detail_response = client.get(f"/api/sessions/{session_id}")

    assert restored_detail_response.status_code == 200
    restored_detail_payload = api_data(restored_detail_response)
    assert restored_detail_payload["title"] == "Renamed Session"
    assert restored_detail_payload["status"] == "cancelled"


def test_session_create_and_update_reject_unknown_project(client: TestClient) -> None:
    create_response = client.post(
        "/api/sessions",
        json={"title": "Invalid Project Session", "project_id": "missing-project"},
    )

    assert create_response.status_code == 404
    assert create_response.json()["detail"] == "Project not found"

    session_response = client.post("/api/sessions", json={"title": "Valid Session"})
    session_id = api_data(session_response)["id"]

    update_response = client.patch(
        f"/api/sessions/{session_id}",
        json={"project_id": "missing-project"},
    )

    assert update_response.status_code == 404
    assert update_response.json()["detail"] == "Project not found"


def test_session_runtime_profile_is_set_and_immutable(client: TestClient) -> None:
    app.state.settings.runtime_profiles_json["strict"] = {
        "allow_network": False,
        "allow_write": False,
        "max_execution_seconds": 60,
        "max_command_length": 512,
    }
    create_response = client.post(
        "/api/sessions",
        json={"title": "Profile Session", "runtime_profile_name": "default"},
    )
    assert create_response.status_code == 201
    session_id = api_data(create_response)["id"]
    assert api_data(create_response)["runtime_profile_name"] == "default"

    update_response = client.patch(
        f"/api/sessions/{session_id}",
        json={"runtime_profile_name": "strict"},
    )
    assert update_response.status_code == 400
    assert (
        update_response.json()["detail"]
        == "runtime_profile_name is immutable after session creation."
    )


def test_session_list_supports_search_filters_and_pagination(client: TestClient) -> None:
    client.post(
        "/api/sessions",
        json={"title": "Alpha Recon", "goal": "inventory", "scenario_type": "web"},
    )
    beta_response = client.post(
        "/api/sessions",
        json={"title": "Beta API", "goal": "api validation", "scenario_type": "api"},
    )
    beta_id = api_data(beta_response)["id"]

    pause_response = client.post(f"/api/sessions/{beta_id}/pause")
    assert pause_response.status_code == 200

    filtered_response = client.get(
        "/api/sessions",
        params={"q": "api", "status": "paused", "page": 1, "page_size": 1, "sort_by": "title"},
    )

    assert filtered_response.status_code == 200
    filtered_payload = filtered_response.json()
    assert filtered_payload["meta"]["pagination"] == {"page": 1, "page_size": 1, "total": 1}
    assert filtered_payload["meta"]["sort"] == {"by": "title", "direction": "desc"}
    assert [session["id"] for session in api_data(filtered_response)] == [beta_id]


def test_session_history_and_artifact_endpoints_support_filters(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "History Session"})
    session_id = api_data(session_response)["id"]

    pause_response = client.post(f"/api/sessions/{session_id}/pause")
    assert pause_response.status_code == 200

    execute_response = client.post(
        "/api/runtime/execute",
        json={
            "session_id": session_id,
            "command": "printf 'history' > reports/history.txt",
            "timeout_seconds": 30,
            "artifact_paths": ["reports/history.txt"],
        },
    )
    assert execute_response.status_code == 200

    history_response = client.get(
        f"/api/sessions/{session_id}/history",
        params={"q": "pause", "page": 1, "page_size": 20},
    )

    assert history_response.status_code == 200
    history_payload = history_response.json()
    assert history_payload["meta"]["pagination"]["total"] >= 1
    assert any("pause" in entry["message"] for entry in api_data(history_response))

    artifact_response = client.get(
        f"/api/sessions/{session_id}/artifacts",
        params={"q": "history.txt"},
    )

    assert artifact_response.status_code == 200
    artifact_payload = artifact_response.json()
    assert artifact_payload["meta"]["pagination"]["total"] == 1
    assert api_data(artifact_response)[0]["relative_path"] == "reports/history.txt"

    runtime_runs_response = client.get("/api/runtime/runs", params={"session_id": session_id})
    assert runtime_runs_response.status_code == 200
    assert api_data(runtime_runs_response)[0]["session_id"] == session_id


def test_session_terminal_endpoints_persist_history_and_emit_events(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Terminal Session"})
    session_id = api_data(session_response)["id"]

    create_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={
            "title": "Ops Shell",
            "shell": "/bin/bash",
            "cwd": "/workspace/sessions/demo",
            "metadata": {"origin": "test"},
        },
    )

    assert create_response.status_code == 201
    created_terminal = api_data(create_response)
    terminal_id = created_terminal["id"]
    assert created_terminal["session_id"] == session_id
    assert created_terminal["title"] == "Ops Shell"
    assert created_terminal["status"] == "open"
    assert created_terminal["shell"] == "/bin/bash"
    assert created_terminal["cwd"] == "/workspace/sessions/demo"
    assert created_terminal["metadata"] == {"origin": "test"}
    assert created_terminal["closed_at"] is None

    list_response = client.get(f"/api/sessions/{session_id}/terminals")
    assert list_response.status_code == 200
    assert [terminal["id"] for terminal in api_data(list_response)] == [terminal_id]

    detail_response = client.get(f"/api/sessions/{session_id}/terminals/{terminal_id}")
    assert detail_response.status_code == 200
    assert api_data(detail_response)["id"] == terminal_id

    history_response = client.get(
        f"/api/sessions/{session_id}/history",
        params={"source": "terminal"},
    )
    assert history_response.status_code == 200
    history_entries = api_data(history_response)
    assert history_entries[0]["event_type"] == "terminal.session.created"
    assert history_entries[0]["payload"]["id"] == terminal_id

    with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
        close_response = client.post(f"/api/sessions/{session_id}/terminals/{terminal_id}/close")
        assert close_response.status_code == 200
        closed_terminal = api_data(close_response)
        assert closed_terminal["status"] == "closed"
        assert closed_terminal["closed_at"] is not None

        event = websocket.receive_json()
        assert event["type"] == "terminal.session.closed"
        assert event["session_id"] == session_id
        assert event["payload"]["id"] == terminal_id
        assert event["payload"]["status"] == "closed"

    repeat_close_response = client.post(f"/api/sessions/{session_id}/terminals/{terminal_id}/close")
    assert repeat_close_response.status_code == 200
    assert api_data(repeat_close_response)["id"] == terminal_id
    assert api_data(repeat_close_response)["status"] == "closed"

    history_after_close = client.get(
        f"/api/sessions/{session_id}/history",
        params={"source": "terminal", "sort_order": "asc"},
    )
    assert history_after_close.status_code == 200
    assert [entry["event_type"] for entry in api_data(history_after_close)] == [
        "terminal.session.created",
        "terminal.session.closed",
    ]


def test_session_terminal_job_endpoints_return_session_scoped_metadata(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Terminal Job Session"})
    session_id = api_data(session_response)["id"]
    other_session_response = client.post("/api/sessions", json={"title": "Other Session"})
    other_session_id = api_data(other_session_response)["id"]

    terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "Job Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]

    other_terminal_response = client.post(
        f"/api/sessions/{other_session_id}/terminals",
        json={"title": "Other Job Shell"},
    )
    other_terminal_id = api_data(other_terminal_response)["id"]

    with DBSession(app.state.database_engine) as db_session:
        repository = TerminalRepository(db_session)
        job = repository.create_terminal_job(
            terminal_session_id=terminal_id,
            session_id=session_id,
            command="sleep 10",
            status=RuntimeTerminalJobStatus.QUEUED,
            metadata={"detach": False, "phase": "skeleton"},
        )
        other_job = repository.create_terminal_job(
            terminal_session_id=other_terminal_id,
            session_id=other_session_id,
            command="echo hidden",
            status=RuntimeTerminalJobStatus.RUNNING,
            metadata={"detach": True},
        )
        job_id = job.id
        other_job_id = other_job.id

    jobs_response = client.get(f"/api/sessions/{session_id}/terminal-jobs")
    assert jobs_response.status_code == 200
    jobs_payload = api_data(jobs_response)
    assert [entry["id"] for entry in jobs_payload] == [job_id]
    assert jobs_payload[0]["session_id"] == session_id
    assert jobs_payload[0]["terminal_session_id"] == terminal_id
    assert jobs_payload[0]["command"] == "sleep 10"
    assert jobs_payload[0]["status"] == "queued"
    assert jobs_payload[0]["metadata"] == {"detach": False, "phase": "skeleton"}

    job_detail_response = client.get(f"/api/sessions/{session_id}/terminal-jobs/{job_id}")
    assert job_detail_response.status_code == 200
    assert api_data(job_detail_response)["id"] == job_id

    missing_job_response = client.get(f"/api/sessions/{session_id}/terminal-jobs/does-not-exist")
    assert missing_job_response.status_code == 404
    assert missing_job_response.json()["detail"] == "Terminal job not found"

    foreign_job_response = client.get(f"/api/sessions/{session_id}/terminal-jobs/{other_job_id}")
    assert foreign_job_response.status_code == 404


def test_session_terminal_rest_controls_require_attached_terminal_and_reuse_live_pty(
    client: TestClient,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Terminal REST Controls"})
    session_id = api_data(session_response)["id"]
    terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "REST Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]

    input_before_attach = client.post(
        f"/api/sessions/{session_id}/terminals/{terminal_id}/input",
        json={"data": "whoami\n"},
    )
    assert input_before_attach.status_code == 409
    assert input_before_attach.json()["detail"] == "Terminal is not currently attached"

    stream_path = f"/api/sessions/{session_id}/terminals/{terminal_id}/stream?cols=80&rows=24"
    with client.websocket_connect(stream_path) as terminal_stream:
        ready_frame = terminal_stream.receive_json()
        assert ready_frame["type"] == "ready"

        input_response = client.post(
            f"/api/sessions/{session_id}/terminals/{terminal_id}/input",
            json={"data": "whoami\n"},
        )
        resize_response = client.post(
            f"/api/sessions/{session_id}/terminals/{terminal_id}/resize",
            json={"cols": 120, "rows": 40},
        )
        interrupt_response = client.post(
            f"/api/sessions/{session_id}/terminals/{terminal_id}/interrupt"
        )

        assert input_response.status_code == 200
        assert api_data(input_response) == {"ok": True}
        assert resize_response.status_code == 200
        assert api_data(resize_response) == {"ok": True}
        assert interrupt_response.status_code == 200
        assert api_data(interrupt_response) == {"ok": True}

        backend_process = app.state.terminal_backend.processes[terminal_id]
        assert backend_process.inputs[-1] == b"whoami\n"
        assert backend_process.resize_history[-1] == (120, 40)
        assert backend_process.signals[-1] == "INT"

        assert terminal_stream.receive_json() == {"type": "output", "data": "whoami\n"}
        assert terminal_stream.receive_json() == {"type": "output", "data": "^C"}


def test_session_terminal_execute_without_detach_reuses_current_terminal_job(
    client: TestClient,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Interactive Execute"})
    session_id = api_data(session_response)["id"]
    terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "Interactive Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]

    stream_path = f"/api/sessions/{session_id}/terminals/{terminal_id}/stream?cols=80&rows=24"
    with client.websocket_connect(stream_path) as terminal_stream:
        ready_frame = terminal_stream.receive_json()
        execute_response = client.post(
            f"/api/sessions/{session_id}/terminals/{terminal_id}/execute",
            json={"command": "pwd", "detach": False},
        )

        assert execute_response.status_code == 200
        assert api_data(execute_response) == {
            "terminal_id": terminal_id,
            "accepted": True,
            "detach": False,
            "job_id": ready_frame["job_id"],
            "status": "running",
        }
        assert app.state.terminal_backend.processes[terminal_id].inputs[-1] == b"pwd\n"
        assert terminal_stream.receive_json() == {"type": "output", "data": "pwd\n"}


def test_session_terminal_execute_detach_tail_and_cleanup_are_session_scoped(
    client: TestClient,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Detached Execute"})
    session_id = api_data(session_response)["id"]
    other_session_response = client.post("/api/sessions", json={"title": "Detached Other"})
    other_session_id = api_data(other_session_response)["id"]
    terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "Detached Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]

    execute_response = client.post(
        f"/api/sessions/{session_id}/terminals/{terminal_id}/execute",
        json={
            "command": "printf 'hello'",
            "detach": True,
            "timeout_seconds": 30,
        },
    )
    assert execute_response.status_code == 200
    job_id = api_data(execute_response)["job_id"]
    assert job_id is not None

    deadline = time.time() + TEST_EVENTUAL_TIMEOUT_SECONDS
    completed_job: dict[str, object] | None = None
    while time.time() < deadline:
        job_response = client.get(f"/api/sessions/{session_id}/terminal-jobs/{job_id}")
        assert job_response.status_code == 200
        completed_job = api_data(job_response)
        assert completed_job is not None
        if completed_job["status"] == "completed":
            break
        time.sleep(TEST_POLL_INTERVAL_SECONDS)
    assert completed_job is not None
    assert completed_job["status"] == "completed"
    metadata = cast(dict[str, object], completed_job["metadata"])
    assert metadata["detach"] is True
    assert "stdout_tail" not in metadata
    assert "stderr_tail" not in metadata
    assert "artifact_paths" not in metadata
    assert "run_id" not in metadata

    tail_response = client.get(
        f"/api/sessions/{session_id}/terminal-jobs/{job_id}/tail",
        params={"stream": "stdout", "lines": 20},
    )
    assert tail_response.status_code == 200
    tail_payload = api_data(tail_response)
    assert tail_payload["job_id"] == job_id
    assert tail_payload["status"] == "completed"
    assert "printf 'hello'" in tail_payload["tail"]

    foreign_tail_response = client.get(
        f"/api/sessions/{other_session_id}/terminal-jobs/{job_id}/tail",
        params={"stream": "stdout", "lines": 20},
    )
    assert foreign_tail_response.status_code == 404

    cleanup_response = client.post(
        f"/api/sessions/{session_id}/terminal-jobs/cleanup",
        json={},
    )
    assert cleanup_response.status_code == 200
    assert api_data(cleanup_response)["deleted_jobs"] == 1

    deleted_job_response = client.get(f"/api/sessions/{session_id}/terminal-jobs/{job_id}")
    assert deleted_job_response.status_code == 404


def test_session_terminal_execute_detach_rejects_second_running_job(
    client: TestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Detached Conflict"})
    session_id = api_data(session_response)["id"]
    terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "Detached Conflict Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]
    backend = app.state.terminal_backend
    original_open_terminal = backend.open_terminal

    class HangingTerminalProcess:
        def __init__(self) -> None:
            self.events: asyncio.Queue[object] = asyncio.Queue()

        async def send_input(self, data: bytes) -> None:
            del data

        async def resize(self, cols: int, rows: int) -> None:
            del cols, rows

        async def send_signal(self, signal_name: str) -> None:
            del signal_name

        async def send_eof(self) -> None:
            await self.events.put(
                terminal_runtime.TerminalBackendEvent.exit(exit_code=0, reason="eof")
            )

        async def close(self, *, reason: str) -> None:
            await self.events.put(
                terminal_runtime.TerminalBackendEvent.exit(exit_code=None, reason=reason)
            )

    async def open_terminal_for_conflict(**kwargs: object) -> object:
        terminal_key = kwargs.get("terminal_id")
        if isinstance(terminal_key, str) and ":job:" in terminal_key:
            return HangingTerminalProcess()
        return await original_open_terminal(**kwargs)

    monkeypatch.setattr(backend, "open_terminal", open_terminal_for_conflict)

    first_execute = client.post(
        f"/api/sessions/{session_id}/terminals/{terminal_id}/execute",
        json={"command": "sleep 60", "detach": True, "timeout_seconds": 60},
    )
    assert first_execute.status_code == 200

    second_execute = client.post(
        f"/api/sessions/{session_id}/terminals/{terminal_id}/execute",
        json={"command": "sleep 30", "detach": True, "timeout_seconds": 60},
    )
    assert second_execute.status_code == 409
    assert second_execute.json()["detail"] == "Terminal already has a running detached job"

    jobs_response = client.get(f"/api/sessions/{session_id}/terminal-jobs")
    jobs_payload = api_data(jobs_response)
    assert len(jobs_payload) == 1


def test_session_terminal_execute_detach_allows_running_interactive_shell(
    client: TestClient,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Interactive Plus Detached"})
    session_id = api_data(session_response)["id"]
    terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "Interactive Plus Detached Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]

    stream_path = f"/api/sessions/{session_id}/terminals/{terminal_id}/stream?cols=120&rows=40"
    with client.websocket_connect(stream_path) as websocket:
        ready_frame = websocket.receive_json()
        assert ready_frame["type"] == "ready"

        execute_response = client.post(
            f"/api/sessions/{session_id}/terminals/{terminal_id}/execute",
            json={"command": "printf 'bg'", "detach": True, "timeout_seconds": 30},
        )
        assert execute_response.status_code == 200
        detached_job_id = api_data(execute_response)["job_id"]
        assert detached_job_id is not None

        deadline = time.time() + TEST_EVENTUAL_TIMEOUT_SECONDS
        jobs_payload: list[dict[str, object]] = []
        while time.time() < deadline:
            jobs_response = client.get(f"/api/sessions/{session_id}/terminal-jobs")
            assert jobs_response.status_code == 200
            jobs_payload = api_data(jobs_response)
            if len(jobs_payload) >= 2 and any(
                job["id"] == detached_job_id and job["status"] == "completed"
                for job in jobs_payload
            ):
                break
            time.sleep(TEST_POLL_INTERVAL_SECONDS)

        assert len(jobs_payload) >= 2
        assert any(job["id"] == detached_job_id for job in jobs_payload)

        websocket.send_json({"type": "close"})
        assert websocket.receive_json()["type"] == "exit"
        assert websocket.receive_json()["type"] == "closed"


def test_session_terminal_stop_endpoint_cancels_live_detached_job(
    client: TestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Detached Stop"})
    session_id = api_data(session_response)["id"]
    terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "Detached Stop Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]
    backend = app.state.terminal_backend
    original_open_terminal = backend.open_terminal

    class HangingTerminalProcess:
        def __init__(self) -> None:
            self.events: asyncio.Queue[object] = asyncio.Queue()
            self.inputs: list[bytes] = []
            self.closed_reasons: list[str] = []

        async def send_input(self, data: bytes) -> None:
            self.inputs.append(data)
            await self.events.put(terminal_runtime.TerminalBackendEvent.output(data=data))

        async def resize(self, cols: int, rows: int) -> None:
            del cols, rows

        async def send_signal(self, signal_name: str) -> None:
            del signal_name

        async def send_eof(self) -> None:
            await self.events.put(
                terminal_runtime.TerminalBackendEvent.exit(exit_code=0, reason="eof")
            )

        async def close(self, *, reason: str) -> None:
            self.closed_reasons.append(reason)
            await self.events.put(
                terminal_runtime.TerminalBackendEvent.exit(exit_code=None, reason=reason)
            )

    async def open_terminal_for_stop(**kwargs: object) -> object:
        terminal_key = kwargs.get("terminal_id")
        if isinstance(terminal_key, str) and ":job:" in terminal_key:
            return HangingTerminalProcess()
        return await original_open_terminal(**kwargs)

    monkeypatch.setattr(backend, "open_terminal", open_terminal_for_stop)

    execute_response = client.post(
        f"/api/sessions/{session_id}/terminals/{terminal_id}/execute",
        json={"command": "sleep 60", "detach": True, "timeout_seconds": 60},
    )
    assert execute_response.status_code == 200
    job_id = api_data(execute_response)["job_id"]
    assert job_id is not None

    stop_response = client.post(f"/api/sessions/{session_id}/terminal-jobs/{job_id}/stop")
    assert stop_response.status_code == 200

    deadline = time.time() + TEST_EVENTUAL_TIMEOUT_SECONDS
    stopped_job: dict[str, object] | None = None
    while time.time() < deadline:
        job_response = client.get(f"/api/sessions/{session_id}/terminal-jobs/{job_id}")
        assert job_response.status_code == 200
        stopped_job = api_data(job_response)
        assert stopped_job is not None
        if stopped_job["status"] == "cancelled":
            break
        time.sleep(TEST_POLL_INTERVAL_SECONDS)
    assert stopped_job is not None
    assert stopped_job["status"] == "cancelled"


def test_session_terminal_stop_endpoint_rejects_non_live_job(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Detached Stop Non Live"})
    session_id = api_data(session_response)["id"]
    terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "Detached Stop Non Live Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]

    execute_response = client.post(
        f"/api/sessions/{session_id}/terminals/{terminal_id}/execute",
        json={"command": "printf 'done'", "detach": True, "timeout_seconds": 30},
    )
    assert execute_response.status_code == 200
    job_id = api_data(execute_response)["job_id"]
    assert job_id is not None

    deadline = time.time() + TEST_EVENTUAL_TIMEOUT_SECONDS
    while time.time() < deadline:
        job_response = client.get(f"/api/sessions/{session_id}/terminal-jobs/{job_id}")
        assert job_response.status_code == 200
        if api_data(job_response)["status"] == "completed":
            break
        time.sleep(TEST_POLL_INTERVAL_SECONDS)

    stop_response = client.post(f"/api/sessions/{session_id}/terminal-jobs/{job_id}/stop")
    assert stop_response.status_code == 409
    assert stop_response.json()["detail"] == "Terminal job is not currently live"


def test_cleanup_finished_jobs_rolls_back_when_run_log_write_fails(
    client: TestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Cleanup Atomicity"})
    session_id = api_data(session_response)["id"]
    terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "Cleanup Atomicity Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]

    with DBSession(app.state.database_engine) as db_session:
        session_repository = SessionRepository(db_session)
        session = session_repository.get_session(session_id)
        assert session is not None
        terminal_repository = TerminalRepository(db_session)
        shell_service = SessionShellService(terminal_repository, RunLogRepository(db_session))
        job_result = shell_service.start_terminal_job(
            session=session,
            terminal_id=terminal_id,
            command="printf 'cleanup'",
            metadata={"detach": True},
        )
        shell_service.finish_terminal_job(
            session=session,
            job_id=job_result.job.id,
            status=RuntimeTerminalJobStatus.COMPLETED,
            exit_code=0,
            metadata_updates={"stdout_tail": "cleanup"},
        )

    def fail_create_log(self: RunLogRepository, **kwargs: object) -> None:
        del self, kwargs
        raise RuntimeError("forced cleanup log failure")

    monkeypatch.setattr(RunLogRepository, "create_log", fail_create_log)

    with DBSession(app.state.database_engine) as db_session:
        session_repository = SessionRepository(db_session)
        session = session_repository.get_session(session_id)
        assert session is not None
        shell_service = SessionShellService(
            TerminalRepository(db_session), RunLogRepository(db_session)
        )
        with pytest.raises(RuntimeError, match="forced cleanup log failure"):
            shell_service.cleanup_finished_jobs(session=session, active_job_ids=set())

    remaining_job = client.get(f"/api/sessions/{session_id}/terminal-jobs")
    assert remaining_job.status_code == 200
    remaining_payload = api_data(remaining_job)
    assert len(remaining_payload) == 1


def test_session_terminal_stream_rejects_second_active_attach(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Terminal Stream Session"})
    session_id = api_data(session_response)["id"]
    terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "Primary Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]

    stream_path = f"/api/sessions/{session_id}/terminals/{terminal_id}/stream?cols=120&rows=40"
    with client.websocket_connect(stream_path) as websocket:
        ready_frame = websocket.receive_json()
        assert ready_frame["type"] == "ready"
        assert ready_frame["session_id"] == session_id
        assert ready_frame["terminal_id"] == terminal_id
        assert ready_frame["job_id"]

        with pytest.raises(WebSocketDenialResponse) as exc_info:
            with client.websocket_connect(stream_path):
                pass

        response = exc_info.value
        assert response.status_code == 409
        assert "already attached" in response.text


def test_session_terminal_stream_supports_concurrent_different_terminals(
    client: TestClient,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Concurrent Terminal Session"})
    session_id = api_data(session_response)["id"]

    first_terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "Alpha Shell"},
    )
    second_terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "Beta Shell"},
    )
    first_terminal_id = api_data(first_terminal_response)["id"]
    second_terminal_id = api_data(second_terminal_response)["id"]

    with ExitStack() as stack:
        alpha = stack.enter_context(
            client.websocket_connect(
                f"/api/sessions/{session_id}/terminals/{first_terminal_id}/stream?cols=100&rows=30"
            )
        )
        beta = stack.enter_context(
            client.websocket_connect(
                f"/api/sessions/{session_id}/terminals/{second_terminal_id}/stream?cols=80&rows=24"
            )
        )

        alpha_ready = alpha.receive_json()
        beta_ready = beta.receive_json()
        assert alpha_ready["terminal_id"] == first_terminal_id
        assert beta_ready["terminal_id"] == second_terminal_id
        assert alpha_ready["job_id"] != beta_ready["job_id"]

        alpha.send_json({"type": "input", "data": "alpha\n"})
        beta.send_json({"type": "input", "data": "beta\n"})

        alpha_output = alpha.receive_json()
        beta_output = beta.receive_json()
        assert alpha_output == {"type": "output", "data": "alpha\n"}
        assert beta_output == {"type": "output", "data": "beta\n"}


def test_session_terminal_stream_persists_job_lifecycle_and_emits_events(
    client: TestClient,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Terminal Lifecycle Session"})
    session_id = api_data(session_response)["id"]
    terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "Lifecycle Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]

    with client.websocket_connect(f"/api/sessions/{session_id}/events") as event_stream:
        with client.websocket_connect(
            f"/api/sessions/{session_id}/terminals/{terminal_id}/stream?cols=80&rows=24"
        ) as terminal_stream:
            ready_frame = terminal_stream.receive_json()
            assert ready_frame["type"] == "ready"
            job_id = ready_frame["job_id"]

            started_event = event_stream.receive_json()
            assert started_event["type"] == "terminal.job.started"
            assert started_event["payload"]["job_id"] == job_id
            assert started_event["payload"]["terminal_id"] == terminal_id

            terminal_stream.send_json({"type": "input", "data": "exit\n"})
            assert terminal_stream.receive_json() == {"type": "output", "data": "exit\n"}

            exit_frame = terminal_stream.receive_json()
            assert exit_frame == {"type": "exit", "exit_code": 0, "reason": "exit"}

            closed_frame = terminal_stream.receive_json()
            assert closed_frame == {"type": "closed", "reason": "exit"}

            completed_event = event_stream.receive_json()
            assert completed_event["type"] == "terminal.job.completed"
            assert completed_event["payload"]["job_id"] == job_id
            assert completed_event["payload"]["exit_code"] == 0

            closed_event = event_stream.receive_json()
            assert closed_event["type"] == "terminal.session.closed"
            assert closed_event["payload"]["id"] == terminal_id

    jobs_response = client.get(f"/api/sessions/{session_id}/terminal-jobs")
    assert jobs_response.status_code == 200
    jobs_payload = api_data(jobs_response)
    assert jobs_payload[0]["id"] == job_id
    assert jobs_payload[0]["status"] == "completed"
    assert jobs_payload[0]["exit_code"] == 0


def test_session_terminal_stream_close_frame_cancels_job_and_emits_events(
    client: TestClient,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Terminal Close Frame Session"})
    session_id = api_data(session_response)["id"]
    terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "Close Frame Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]

    with client.websocket_connect(f"/api/sessions/{session_id}/events") as event_stream:
        with client.websocket_connect(
            f"/api/sessions/{session_id}/terminals/{terminal_id}/stream?cols=80&rows=24"
        ) as terminal_stream:
            ready_frame = terminal_stream.receive_json()
            job_id = ready_frame["job_id"]

            started_event = event_stream.receive_json()
            assert started_event["type"] == "terminal.job.started"
            assert started_event["payload"]["job_id"] == job_id

            terminal_stream.send_json({"type": "close"})

            exit_frame = terminal_stream.receive_json()
            assert exit_frame == {"type": "exit", "exit_code": None, "reason": "close"}
            closed_frame = terminal_stream.receive_json()
            assert closed_frame == {"type": "closed", "reason": "close"}

            cancelled_event = event_stream.receive_json()
            assert cancelled_event["type"] == "terminal.job.cancelled"
            assert cancelled_event["payload"]["job_id"] == job_id

            closed_event = event_stream.receive_json()
            assert closed_event["type"] == "terminal.session.closed"
            assert closed_event["payload"]["id"] == terminal_id

    jobs_response = client.get(f"/api/sessions/{session_id}/terminal-jobs")
    jobs_payload = api_data(jobs_response)
    assert jobs_payload[0]["id"] == job_id
    assert jobs_payload[0]["status"] == "cancelled"


def test_http_terminal_close_closes_active_stream_and_cancels_job(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "HTTP Close Active Stream"})
    session_id = api_data(session_response)["id"]
    terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "HTTP Close Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]

    stream_path = f"/api/sessions/{session_id}/terminals/{terminal_id}/stream?cols=80&rows=24"
    with client.websocket_connect(stream_path) as terminal_stream:
        ready_frame = terminal_stream.receive_json()
        job_id = ready_frame["job_id"]

        close_response = client.post(f"/api/sessions/{session_id}/terminals/{terminal_id}/close")
        assert close_response.status_code == 200
        assert api_data(close_response)["status"] == "closed"

        exit_frame = terminal_stream.receive_json()
        assert exit_frame == {"type": "exit", "exit_code": None, "reason": "close"}
        closed_frame = terminal_stream.receive_json()
        assert closed_frame == {"type": "closed", "reason": "close"}

    jobs_response = client.get(f"/api/sessions/{session_id}/terminal-jobs")
    jobs_payload = api_data(jobs_response)
    assert jobs_payload[0]["id"] == job_id
    assert jobs_payload[0]["status"] == "cancelled"


def test_session_terminal_stream_rejects_invalid_signal_name(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Invalid Signal Session"})
    session_id = api_data(session_response)["id"]
    terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "Signal Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]

    stream_path = f"/api/sessions/{session_id}/terminals/{terminal_id}/stream?cols=80&rows=24"
    with client.websocket_connect(stream_path) as terminal_stream:
        terminal_stream.receive_json()
        terminal_stream.send_json({"type": "signal", "signal": "TERM;whoami"})

        error_frame = terminal_stream.receive_json()
        assert error_frame == {"type": "error", "message": "unsupported terminal signal"}


def test_session_terminal_stream_rejects_malformed_json_frame(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Malformed Frame Session"})
    session_id = api_data(session_response)["id"]
    terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": "Malformed Frame Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]

    stream_path = f"/api/sessions/{session_id}/terminals/{terminal_id}/stream?cols=80&rows=24"
    with client.websocket_connect(stream_path) as terminal_stream:
        terminal_stream.receive_json()
        terminal_stream.send_text("not-json")

        error_frame = terminal_stream.receive_json()
        assert error_frame == {"type": "error", "message": "terminal frames must be valid JSON"}

        terminal_stream.send_json({"type": "close"})
        assert terminal_stream.receive_json() == {
            "type": "exit",
            "exit_code": None,
            "reason": "close",
        }
        assert terminal_stream.receive_json() == {"type": "closed", "reason": "close"}


def test_session_terminal_stream_rejects_foreign_session_reattach_during_grace(
    client: TestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(app.state.settings, "terminal_disconnect_grace_seconds", 0.2)

    owner_session_response = client.post("/api/sessions", json={"title": "Owner Session"})
    owner_session_id = api_data(owner_session_response)["id"]
    foreign_session_response = client.post("/api/sessions", json={"title": "Foreign Session"})
    foreign_session_id = api_data(foreign_session_response)["id"]
    terminal_response = client.post(
        f"/api/sessions/{owner_session_id}/terminals",
        json={"title": "Detached Grace Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]

    owner_stream_path = (
        f"/api/sessions/{owner_session_id}/terminals/{terminal_id}/stream?cols=80&rows=24"
    )
    with client.websocket_connect(owner_stream_path) as owner_stream:
        owner_stream.receive_json()

    foreign_stream_path = (
        f"/api/sessions/{foreign_session_id}/terminals/{terminal_id}/stream?cols=80&rows=24"
    )
    with pytest.raises(WebSocketDenialResponse) as exc_info:
        with client.websocket_connect(foreign_stream_path):
            pass

    response = exc_info.value
    assert response.status_code == 404
    assert "Terminal not found" in response.text

    jobs_response = client.get(f"/api/sessions/{owner_session_id}/terminal-jobs")
    jobs_payload = api_data(jobs_response)
    assert len(jobs_payload) == 1

    time.sleep(0.25)


@pytest.mark.parametrize(
    ("field", "value"),
    [("shell", "   "), ("cwd", "\n\t  ")],
)
def test_session_terminal_create_rejects_blank_shell_or_cwd(
    client: TestClient,
    field: str,
    value: str,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Terminal Validation Session"})
    session_id = api_data(session_response)["id"]

    payload = {
        "title": "Validated Shell",
        "shell": "/bin/bash",
        "cwd": "/workspace/demo",
        "metadata": {"origin": "test"},
    }
    payload[field] = value

    create_response = client.post(f"/api/sessions/{session_id}/terminals", json=payload)

    assert create_response.status_code == 422
    assert create_response.json()["detail"] == "Request validation failed"

    list_response = client.get(f"/api/sessions/{session_id}/terminals")
    assert list_response.status_code == 200
    assert api_data(list_response) == []


def test_session_terminal_create_rejects_oversized_metadata(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Terminal Metadata Limits"})
    session_id = api_data(session_response)["id"]

    create_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={
            "title": "Oversized Metadata Shell",
            "shell": "/bin/bash",
            "cwd": "/workspace/demo",
            "metadata": {"payload": "x" * 5000},
        },
    )

    assert create_response.status_code == 422
    assert create_response.json()["detail"] == "Request validation failed"

    list_response = client.get(f"/api/sessions/{session_id}/terminals")
    assert list_response.status_code == 200
    assert api_data(list_response) == []


def test_terminal_repository_rejects_job_session_mismatch(client: TestClient) -> None:
    session_response = client.post(
        "/api/sessions", json={"title": "Terminal Job Integrity Session"}
    )
    session_id = api_data(session_response)["id"]
    other_session_response = client.post("/api/sessions", json={"title": "Other Integrity Session"})
    other_session_id = api_data(other_session_response)["id"]

    terminal_response = client.post(f"/api/sessions/{session_id}/terminals", json={"title": "Main"})
    terminal_id = api_data(terminal_response)["id"]

    with DBSession(app.state.database_engine) as db_session:
        repository = TerminalRepository(db_session)
        with pytest.raises(ValueError, match="does not belong to the provided session"):
            repository.create_terminal_job(
                terminal_session_id=terminal_id,
                session_id=other_session_id,
                command="echo mismatch",
                status=RuntimeTerminalJobStatus.QUEUED,
            )


def test_terminal_repository_rejects_job_for_closed_terminal(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Closed Terminal Job Session"})
    session_id = api_data(session_response)["id"]

    terminal_response = client.post(f"/api/sessions/{session_id}/terminals", json={"title": "Main"})
    terminal_id = api_data(terminal_response)["id"]

    close_response = client.post(f"/api/sessions/{session_id}/terminals/{terminal_id}/close")
    assert close_response.status_code == 200

    with DBSession(app.state.database_engine) as db_session:
        repository = TerminalRepository(db_session)
        with pytest.raises(ValueError, match="closed and cannot accept new jobs"):
            repository.create_terminal_job(
                terminal_session_id=terminal_id,
                session_id=session_id,
                command="echo closed",
                status=RuntimeTerminalJobStatus.QUEUED,
            )


@pytest.mark.parametrize(
    ("status", "expects_started_at"),
    [
        (RuntimeTerminalJobStatus.QUEUED, False),
        (RuntimeTerminalJobStatus.RUNNING, True),
    ],
)
def test_terminal_repository_accepts_only_valid_initial_job_states(
    client: TestClient,
    status: RuntimeTerminalJobStatus,
    expects_started_at: bool,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Job State Session"})
    session_id = api_data(session_response)["id"]

    terminal_response = client.post(f"/api/sessions/{session_id}/terminals", json={"title": "Main"})
    terminal_id = api_data(terminal_response)["id"]

    with DBSession(app.state.database_engine) as db_session:
        repository = TerminalRepository(db_session)
        job = repository.create_terminal_job(
            terminal_session_id=terminal_id,
            session_id=session_id,
            command="sleep 1",
            status=status,
            metadata={"nested": {"phase": "phase-1.1"}},
        )

        assert job.status == status
        assert (job.started_at is not None) is expects_started_at
        assert job.ended_at is None
        assert job.exit_code is None
        assert job.metadata_json == {"nested": {"phase": "phase-1.1"}}


@pytest.mark.parametrize(
    "status",
    [
        RuntimeTerminalJobStatus.COMPLETED,
        RuntimeTerminalJobStatus.FAILED,
        RuntimeTerminalJobStatus.CANCELLED,
    ],
)
def test_terminal_repository_rejects_invalid_initial_job_states(
    client: TestClient,
    status: RuntimeTerminalJobStatus,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Invalid Job State Session"})
    session_id = api_data(session_response)["id"]

    terminal_response = client.post(f"/api/sessions/{session_id}/terminals", json={"title": "Main"})
    terminal_id = api_data(terminal_response)["id"]

    with DBSession(app.state.database_engine) as db_session:
        repository = TerminalRepository(db_session)
        with pytest.raises(ValueError, match="can only be created in queued or running state"):
            repository.create_terminal_job(
                terminal_session_id=terminal_id,
                session_id=session_id,
                command="echo invalid",
                status=status,
            )

        assert repository.list_terminal_jobs(session_id=session_id) == []


@pytest.mark.parametrize(
    "metadata",
    [
        {"score": float("nan")},
        {"payload": "x" * 5000},
        [],
        "",
        0,
        False,
    ],
)
def test_terminal_repository_rejects_invalid_job_metadata(
    client: TestClient,
    metadata: object,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Invalid Job Metadata Session"})
    session_id = api_data(session_response)["id"]

    terminal_response = client.post(f"/api/sessions/{session_id}/terminals", json={"title": "Main"})
    terminal_id = api_data(terminal_response)["id"]

    with DBSession(app.state.database_engine) as db_session:
        repository = TerminalRepository(db_session)
        with pytest.raises(ValueError, match="Terminal job metadata"):
            repository.create_terminal_job(
                terminal_session_id=terminal_id,
                session_id=session_id,
                command="echo invalid metadata",
                status=RuntimeTerminalJobStatus.QUEUED,
                metadata=metadata,
            )

        assert repository.list_terminal_jobs(session_id=session_id) == []


def test_terminal_repository_rolls_back_when_job_commit_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Job Commit Rollback Session"})
    session_id = api_data(session_response)["id"]

    terminal_response = client.post(f"/api/sessions/{session_id}/terminals", json={"title": "Main"})
    terminal_id = api_data(terminal_response)["id"]

    rollback_calls = 0

    def fail_commit(self: DBSession) -> None:
        del self
        raise SQLAlchemyError("forced job commit failure")

    original_rollback = DBSession.rollback

    def tracking_rollback(self: DBSession) -> None:
        nonlocal rollback_calls
        rollback_calls += 1
        original_rollback(self)

    monkeypatch.setattr(DBSession, "commit", fail_commit)
    monkeypatch.setattr(DBSession, "rollback", tracking_rollback)

    with DBSession(app.state.database_engine) as db_session:
        repository = TerminalRepository(db_session)
        with pytest.raises(SQLAlchemyError, match="forced job commit failure"):
            repository.create_terminal_job(
                terminal_session_id=terminal_id,
                session_id=session_id,
                command="sleep 5",
                status=RuntimeTerminalJobStatus.QUEUED,
                metadata={"phase": "rollback"},
            )

    assert rollback_calls == 1

    with DBSession(app.state.database_engine) as verification_session:
        verification_repository = TerminalRepository(verification_session)
        assert verification_repository.list_terminal_jobs(session_id=session_id) == []


def test_flush_pending_rolls_back_on_flush_failure() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.rollback_calls = 0

        def flush(self) -> None:
            raise SQLAlchemyError("forced job flush failure")

        def rollback(self) -> None:
            self.rollback_calls += 1

    fake_session = FakeSession()

    with pytest.raises(SQLAlchemyError, match="forced job flush failure"):
        _flush_pending(cast(DBSession, fake_session))

    assert fake_session.rollback_calls == 1


def test_commit_and_refresh_rolls_back_on_refresh_failure() -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.rollback_calls = 0
            self.commit_calls = 0
            self.refresh_calls = 0

        def commit(self) -> None:
            self.commit_calls += 1

        def refresh(self, instance: object) -> None:
            del instance
            self.refresh_calls += 1
            raise SQLAlchemyError("forced job refresh failure")

        def rollback(self) -> None:
            self.rollback_calls += 1

    fake_session = FakeSession()

    _commit_and_refresh(cast(DBSession, fake_session), object())

    assert fake_session.commit_calls == 1
    assert fake_session.refresh_calls == 1
    assert fake_session.rollback_calls == 1


def test_terminal_repository_preserves_job_when_refresh_fails_after_commit(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Job Refresh Semantics Session"})
    session_id = api_data(session_response)["id"]

    terminal_response = client.post(f"/api/sessions/{session_id}/terminals", json={"title": "Main"})
    terminal_id = api_data(terminal_response)["id"]

    refresh_calls = 0
    rollback_calls = 0

    def fail_refresh(self: DBSession, instance: object) -> None:
        del self, instance
        nonlocal refresh_calls
        refresh_calls += 1
        raise SQLAlchemyError("forced job refresh failure")

    original_rollback = DBSession.rollback

    def tracking_rollback(self: DBSession) -> None:
        nonlocal rollback_calls
        rollback_calls += 1
        original_rollback(self)

    monkeypatch.setattr(DBSession, "refresh", fail_refresh)
    monkeypatch.setattr(DBSession, "rollback", tracking_rollback)

    with DBSession(app.state.database_engine) as db_session:
        repository = TerminalRepository(db_session)
        terminal_job = repository.create_terminal_job(
            terminal_session_id=terminal_id,
            session_id=session_id,
            command="sleep 5",
            status=RuntimeTerminalJobStatus.QUEUED,
            metadata={"phase": "refresh-semantics"},
        )
        persisted_job_id = terminal_job.id

    assert persisted_job_id is not None
    assert refresh_calls == 1
    assert rollback_calls == 1

    with DBSession(app.state.database_engine) as verification_session:
        verification_repository = TerminalRepository(verification_session)
        persisted_jobs = verification_repository.list_terminal_jobs(session_id=session_id)
        assert len(persisted_jobs) == 1
        assert persisted_jobs[0].id == persisted_job_id
        assert persisted_jobs[0].command == "sleep 5"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cwd", "/workspace/../../etc"),
        ("shell", "/bin/bash\n--noprofile"),
    ],
)
def test_session_terminal_create_rejects_unsafe_shell_or_cwd(
    client: TestClient,
    field: str,
    value: str,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Terminal Safety Session"})
    session_id = api_data(session_response)["id"]

    payload = {
        "title": "Safe Shell",
        "shell": "/bin/bash",
        "cwd": "/workspace/demo",
        "metadata": {"origin": "test"},
    }
    payload[field] = value

    create_response = client.post(f"/api/sessions/{session_id}/terminals", json=payload)

    assert create_response.status_code == 422
    assert create_response.json()["detail"] == "Request validation failed"

    list_response = client.get(f"/api/sessions/{session_id}/terminals")
    assert list_response.status_code == 200
    assert api_data(list_response) == []


def test_session_terminal_create_rejects_non_finite_metadata(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Terminal Metadata JSON"})
    session_id = api_data(session_response)["id"]

    create_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        content=(
            '{"title":"Non Finite Metadata Shell","shell":"/bin/bash",'
            '"cwd":"/workspace/demo","metadata":{"score":NaN}}'
        ),
        headers={"content-type": "application/json"},
    )

    assert create_response.status_code == 422
    assert create_response.json()["detail"] == "Request validation failed"

    list_response = client.get(f"/api/sessions/{session_id}/terminals")
    assert list_response.status_code == 200
    assert api_data(list_response) == []


def test_session_terminal_create_rolls_back_when_run_log_write_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Terminal Rollback Session"})
    session_id = api_data(session_response)["id"]

    def fail_create_log(self: RunLogRepository, **kwargs: object) -> None:
        del self, kwargs
        raise RuntimeError("forced terminal run-log failure")

    monkeypatch.setattr(RunLogRepository, "create_log", fail_create_log)

    with pytest.raises(RuntimeError, match="forced terminal run-log failure"):
        client.post(
            f"/api/sessions/{session_id}/terminals",
            json={
                "title": "Rollback Shell",
                "shell": "/bin/bash",
                "cwd": "/workspace/demo",
            },
        )

    list_response = client.get(f"/api/sessions/{session_id}/terminals")
    assert list_response.status_code == 200
    assert api_data(list_response) == []


def test_chat_persists_messages_and_attachments(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Chat Session"})
    session_id = api_data(session_response)["id"]

    chat_response = client.post(
        f"/api/sessions/{session_id}/chat",
        json={
            "content": "  investigate target scope  ",
            "wait_for_completion": True,
            "attachments": [
                {
                    "id": "file-1",
                    "name": "scope.txt",
                    "content_type": "text/plain",
                    "size_bytes": 12,
                }
            ],
        },
    )

    assert chat_response.status_code == 200
    chat_payload = api_data(chat_response)
    assert chat_payload["session"]["status"] == "done"
    assert chat_payload["user_message"]["role"] == "user"
    assert chat_payload["assistant_message"]["role"] == "assistant"
    assert chat_payload["assistant_message"]["content"] == (
        "Test assistant reply: investigate target scope (1 attachments)"
    )

    detail_response = client.get(f"/api/sessions/{session_id}")

    assert detail_response.status_code == 200
    detail_payload = api_data(detail_response)
    assert [message["role"] for message in detail_payload["messages"]] == ["user", "assistant"]
    assert detail_payload["messages"][0]["attachments"] == [
        {
            "id": "file-1",
            "name": "scope.txt",
            "content_type": "text/plain",
            "size_bytes": 12,
        }
    ]


def test_chat_defaults_to_non_blocking_enqueue_and_persists_timeline(client: TestClient) -> None:
    class SlowAcceptedChatRuntime:
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
            del (
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                execute_tool,
            )
            assert callbacks is not None
            assert callbacks.on_text_delta is not None
            await _yield_control()
            await callbacks.on_text_delta("queued ")
            await _yield_control()
            await callbacks.on_text_delta("reply")
            return f"queued reply for {content}"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: SlowAcceptedChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Accepted Session"})
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "accepted prompt", "attachments": []},
        )
        assert chat_response.status_code == 200
        chat_payload = api_data(chat_response)
        assert chat_payload["assistant_message"]["content"] == ""
        assert chat_payload["generation"]["status"] == "queued"
        assert chat_payload["queue_position"] == 1
        assert chat_payload["active_generation_id"] is None
        assert chat_payload["queued_generation_count"] == 1
        assert chat_payload["generation"]["steps"][0]["kind"] == "status"
        assert chat_payload["generation"]["steps"][0]["state"] == "queued"

        conversation_payload = None
        for _ in range(int(TEST_EVENTUAL_TIMEOUT_SECONDS / TEST_POLL_INTERVAL_SECONDS)):
            conversation_response = client.get(f"/api/sessions/{session_id}/conversation")
            conversation_payload = api_data(conversation_response)
            generations = conversation_payload["generations"]
            if generations and generations[0]["status"] == "completed":
                break
            time.sleep(TEST_POLL_INTERVAL_SECONDS)

        assert conversation_payload is not None
        assert conversation_payload["active_generation_id"] is None
        assert conversation_payload["queued_generation_count"] == 0
        assert conversation_payload["generations"][0]["status"] == "completed"
        steps = conversation_payload["generations"][0]["steps"]
        assert [step["sequence"] for step in steps] == list(range(1, len(steps) + 1))
        assert any(
            step["kind"] == "output" and step["delta_text"] == "queued reply for accepted prompt"
            for step in steps
        )
        assert any(step["kind"] == "status" and step["state"] == "completed" for step in steps)
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_active_generation_injects_running_context_without_queueing_new_generation(
    client: TestClient,
) -> None:
    class InjectAwareChatRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                execute_tool,
            )
            assert callbacks is not None
            assert callbacks.on_text_delta is not None
            assert callbacks.consume_context_injections is not None
            assert callbacks.on_context_injection_applied is not None

            await callbacks.on_text_delta("初始分析")

            injections: list[str] = []
            deadline = time.time() + TEST_EVENTUAL_TIMEOUT_SECONDS
            while time.time() < deadline:
                injections = await callbacks.consume_context_injections()
                if injections:
                    break
                await _yield_control()

            if injections:
                await callbacks.on_context_injection_applied(injections)
                await callbacks.on_text_delta("后续结论")
                return "初始分析后续结论"

            return "初始分析"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: InjectAwareChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Running Injection Session"})
        session_id = api_data(session_response)["id"]

        chat_response_box, worker = _post_chat_in_thread(
            client,
            session_id,
            {"content": "先分析这个目标", "attachments": [], "wait_for_completion": True},
        )

        inject_response = None
        deadline = time.time() + TEST_EVENTUAL_TIMEOUT_SECONDS
        while time.time() < deadline:
            inject_response = client.post(
                f"/api/sessions/{session_id}/generations/active/inject",
                json={"content": "请额外关注 host-b 的横向移动迹象"},
            )
            if inject_response.status_code == 200:
                break
            time.sleep(TEST_POLL_INTERVAL_SECONDS)
        else:
            pytest.fail("running injection endpoint never accepted the request")

        worker.join(timeout=5)
        assert chat_response_box["value"] is not None
        assert chat_response_box["value"].status_code == 200
        assert inject_response is not None
        inject_payload = api_data(inject_response)
        assert inject_payload["delivery"] == "running_checkpoint"
        assert inject_payload["queued_injection_count"] == 1

        conversation_response = client.get(f"/api/sessions/{session_id}/conversation")
        assert conversation_response.status_code == 200
        conversation_payload = api_data(conversation_response)
        assert conversation_payload["queued_generation_count"] == 0
        assert len(conversation_payload["generations"]) == 1
        assert [message["role"] for message in conversation_payload["messages"]] == [
            "user",
            "assistant",
        ]

        assistant_message = next(
            message
            for message in reversed(conversation_payload["messages"])
            if message["role"] == "assistant"
        )
        output_segments = [
            segment
            for segment in assistant_message["assistant_transcript"]
            if segment["kind"] == "output"
        ]
        assert assistant_message["content"] == "初始分析后续结论"
        assert [segment["text"] for segment in output_segments] == ["初始分析", "后续结论"]
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_builds_multi_turn_context_and_exposes_conversation_reads(client: TestClient) -> None:
    class RecordingChatRuntime:
        def __init__(self) -> None:
            self.histories: list[list[str]] = []

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
            del (
                content,
                attachments,
                available_skills,
                skill_context_prompt,
                execute_tool,
                callbacks,
            )
            assert conversation_messages is not None
            history = [str(getattr(message, "content")) for message in conversation_messages]
            self.histories.append(history)
            return "history => " + " | ".join(history)

    runtime = RecordingChatRuntime()
    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: runtime

    try:
        session_response = client.post("/api/sessions", json={"title": "Context Session"})
        session_id = api_data(session_response)["id"]

        first_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "first question", "attachments": [], "wait_for_completion": True},
        )
        assert first_response.status_code == 200
        second_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "follow-up", "attachments": [], "wait_for_completion": True},
        )
        assert second_response.status_code == 200

        assert runtime.histories == [
            ["first question"],
            ["first question", "history => first question", "follow-up"],
        ]

        conversation_response = client.get(f"/api/sessions/{session_id}/conversation")
        assert conversation_response.status_code == 200
        conversation_payload = api_data(conversation_response)
        assert conversation_payload["active_branch"]["id"] == session_id
        assert [message["content"] for message in conversation_payload["messages"]] == [
            "first question",
            "history => first question",
            "follow-up",
            "history => first question | history => first question | follow-up",
        ]
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_edit_regenerate_fork_rollback_and_replay_endpoints(client: TestClient) -> None:
    class BranchingChatRuntime:
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
            del (
                content,
                attachments,
                available_skills,
                skill_context_prompt,
                execute_tool,
                callbacks,
            )
            assert conversation_messages is not None and conversation_messages
            return f"reply[{getattr(conversation_messages[-1], 'content')}]"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: BranchingChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Branch Session"})
        session_id = api_data(session_response)["id"]

        first_chat = api_data(
            client.post(
                f"/api/sessions/{session_id}/chat",
                json={"content": "alpha", "attachments": [], "wait_for_completion": True},
            )
        )
        api_data(
            client.post(
                f"/api/sessions/{session_id}/chat",
                json={"content": "beta", "attachments": [], "wait_for_completion": True},
            )
        )

        edit_response = client.post(
            f"/api/sessions/{session_id}/messages/{first_chat['user_message']['id']}/edit",
            json={"content": "alpha edited", "attachments": []},
        )
        assert edit_response.status_code == 200
        edit_payload = api_data(edit_response)
        assert edit_payload["user_message"]["content"] == "alpha edited"
        assert edit_payload["assistant_message"]["content"] == "reply[alpha edited]"

        conversation_after_edit = api_data(client.get(f"/api/sessions/{session_id}/conversation"))
        assert [message["content"] for message in conversation_after_edit["messages"]] == [
            "alpha edited",
            "reply[alpha edited]",
        ]

        regenerate_response = client.post(
            f"/api/sessions/{session_id}/messages/{edit_payload['assistant_message']['id']}/regenerate"
        )
        assert regenerate_response.status_code == 200
        regenerate_payload = api_data(regenerate_response)
        assert (
            regenerate_payload["assistant_message"]["id"] != edit_payload["assistant_message"]["id"]
        )
        assert (
            regenerate_payload["assistant_message"]["version_group_id"]
            == edit_payload["assistant_message"]["version_group_id"]
        )

        fork_response = client.post(
            f"/api/sessions/{session_id}/messages/{edit_payload['user_message']['id']}/fork",
            json={"name": "alt-branch"},
        )
        assert fork_response.status_code == 200
        fork_payload = api_data(fork_response)
        assert fork_payload["active_branch"]["id"] != session_id
        assert fork_payload["active_branch"]["name"] == "alt-branch"
        assert [message["content"] for message in fork_payload["messages"]] == ["alpha edited"]

        branch_chat = api_data(
            client.post(
                f"/api/sessions/{session_id}/chat",
                json={"content": "fork prompt", "attachments": [], "wait_for_completion": True},
            )
        )
        assert branch_chat["assistant_message"]["content"] == "reply[fork prompt]"

        rollback_target_id = fork_payload["messages"][0]["id"]
        rollback_response = client.post(
            f"/api/sessions/{session_id}/messages/{rollback_target_id}/rollback"
        )
        assert rollback_response.status_code == 200
        rollback_payload = api_data(rollback_response)
        assert [message["content"] for message in rollback_payload["messages"]] == ["alpha edited"]

        replay_response = client.get(f"/api/sessions/{session_id}/replay")
        assert replay_response.status_code == 200
        replay_payload = api_data(replay_response)
        assert len(replay_payload["branches"]) >= 2
        assert any(message["status"] == "superseded" for message in replay_payload["messages"])
        assert len(replay_payload["generations"]) >= 4
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_cancel_generation_endpoint_and_queue_reads(client: TestClient) -> None:
    second_request_started = threading.Event()

    class SlowQueueChatRuntime:
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
            del (
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                execute_tool,
            )
            assert callbacks is not None
            assert callbacks.on_text_delta is not None
            for chunk in [f"{content}-1 ", f"{content}-2 ", f"{content}-3"]:
                if content == "first":
                    second_request_started.wait(timeout=1)
                if callbacks.is_cancelled is not None and callbacks.is_cancelled():
                    raise asyncio.CancelledError
                await callbacks.on_text_delta(chunk)
                await asyncio.sleep(0.15)
            return f"{content}-done"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: SlowQueueChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Queue Cancel Session"})
        session_id = api_data(session_response)["id"]

        with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
            first_response_box, first_worker = _post_chat_in_thread(
                client,
                session_id,
                {"content": "first", "attachments": [], "wait_for_completion": True},
            )

            second_response_box: dict[str, Response | None] | None = None
            second_worker = None
            active_generation_id: str | None = None

            while True:
                event = websocket.receive_json()
                if event["type"] == "generation.started" and active_generation_id is None:
                    active_generation_id = event["payload"]["generation_id"]
                    second_response_box, second_worker = _post_chat_in_thread(
                        client,
                        session_id,
                        {"content": "second", "attachments": [], "wait_for_completion": True},
                    )
                    second_request_started.set()
                    break

            queue_payload = None
            for _ in range(int(2 / TEST_POLL_INTERVAL_SECONDS)):
                queue_response = client.get(f"/api/sessions/{session_id}/queue")
                queue_payload = api_data(queue_response)
                if (
                    queue_payload["active_generation"] is not None
                    and len(queue_payload["queued_generations"]) == 1
                ):
                    break
                time.sleep(TEST_POLL_INTERVAL_SECONDS)

            assert queue_payload is not None
            assert queue_payload["active_generation"] is not None
            assert len(queue_payload["queued_generations"]) == 1
            assert queue_payload["active_generation_id"] == queue_payload["active_generation"]["id"]
            assert queue_payload["queued_generation_count"] == 1
            assert queue_payload["queued_generations"][0]["queue_position"] == 1
            active_generation_id = queue_payload["active_generation"]["id"]

            cancel_response = client.post(
                f"/api/sessions/{session_id}/generations/{active_generation_id}/cancel"
            )
            assert cancel_response.status_code == 200
            assert api_data(cancel_response)["status"] == "cancelled"

            saw_cancelled = False
            while True:
                event = websocket.receive_json()
                if event["type"] == "generation.cancelled":
                    saw_cancelled = True
                if (
                    saw_cancelled
                    and event["type"] == "session.updated"
                    and event["payload"].get("status") == "done"
                ):
                    break

        first_worker.join(timeout=5)
        assert first_response_box["value"] is not None
        assert first_response_box["value"].status_code == 409

        assert second_worker is not None and second_response_box is not None
        second_worker.join(timeout=5)
        assert second_response_box["value"] is not None
        assert second_response_box["value"].status_code == 200

        final_queue = api_data(client.get(f"/api/sessions/{session_id}/queue"))
        assert final_queue["active_generation"] is None
        assert final_queue["queued_generations"] == []
        assert final_queue["active_generation_id"] is None
        assert final_queue["queued_generation_count"] == 0
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_websocket_streams_session_events(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Streaming Session"})
    session_id = api_data(session_response)["id"]

    with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
        chat_response_box, worker = _post_chat_in_thread(
            client,
            session_id,
            {"content": "hello websocket", "attachments": [], "wait_for_completion": True},
        )

        events = []
        saw_partial_update = False
        while True:
            event = websocket.receive_json()
            events.append(event)
            if (
                event["type"] == "message.updated"
                and event["payload"].get("content")
                != "Test assistant reply: hello websocket (0 attachments)"
            ):
                saw_partial_update = True

            if event["type"] == "session.updated" and event["payload"].get("status") == "done":
                break

        worker.join(timeout=5)
        chat_response = chat_response_box["value"]
        assert chat_response is not None
        assert chat_response.status_code == 200

    event_types = [event["type"] for event in events]
    assert event_types[:4] == [
        "session.updated",
        "message.created",
        "message.created",
        "generation.started",
    ]
    assert event_types[-1] == "session.updated"
    assert "assistant.summary" not in event_types
    assert "message.delta" in event_types
    assert "message.updated" in event_types
    assert "message.completed" in event_types
    assert "assistant.trace" in event_types
    assert saw_partial_update is True
    assert [events[1]["payload"].get("role"), events[2]["payload"].get("role")] == [
        "user",
        "assistant",
    ]
    assert isinstance(events[1]["payload"].get("created_at"), str)
    assert events[2]["payload"]["content"] == ""
    assert events[3]["payload"]["user_message_id"] == events[1]["payload"]["message_id"]
    assert events[3]["payload"]["message_id"] == events[2]["payload"]["message_id"]
    completed_index = event_types.index("message.completed")
    assert (
        events[completed_index]["payload"]["content"]
        == "Test assistant reply: hello websocket (0 attachments)"
    )
    assert isinstance(events[completed_index]["payload"]["assistant_transcript"], list)
    assert any(
        isinstance(event["payload"].get("assistant_transcript"), list)
        for event in events
        if event["type"] == "message.updated"
    )


def test_websocket_supports_replay_cursor(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Replay Cursor Session"})
    session_id = api_data(session_response)["id"]

    chat_response = client.post(
        f"/api/sessions/{session_id}/chat",
        json={"content": "seed replay", "attachments": [], "wait_for_completion": True},
    )
    assert chat_response.status_code == 200

    replayed_events: list[dict[str, object]] = []
    with client.websocket_connect(f"/api/sessions/{session_id}/events?cursor=0") as websocket:
        while True:
            event = websocket.receive_json()
            replayed_events.append(event)
            if event["type"] == "session.updated" and event["payload"].get("status") == "done":
                break

    assert replayed_events
    typed_cursors: list[int] = []
    for event in replayed_events:
        cursor_value = event.get("cursor")
        if isinstance(cursor_value, int):
            typed_cursors.append(cursor_value)
    assert typed_cursors
    assert typed_cursors == sorted(typed_cursors)
    max_cursor = int(typed_cursors[-1])

    with client.websocket_connect(
        f"/api/sessions/{session_id}/events?cursor={max_cursor}"
    ) as websocket:
        pause_response = client.post(f"/api/sessions/{session_id}/pause")
        assert pause_response.status_code == 200
        replay_event = websocket.receive_json()

    assert replay_event["type"] == "session.updated"
    assert replay_event["payload"]["status"] == "paused"
    assert isinstance(replay_event.get("cursor"), int)
    assert int(replay_event["cursor"]) > max_cursor


def test_session_context_window_and_manual_compaction_emit_persisted_events(
    client: TestClient,
) -> None:
    class _FakeSkillService:
        def list_loaded_skills_for_agent(self, **_: object) -> list[object]:
            return []

    class _FakeCapabilityFacade:
        def build_prompt_fragments(self, **_: object) -> dict[str, str]:
            return {
                "inventory_summary": "",
                "schema_summary": "",
                "prompt_fragment": "",
            }

        def build_mcp_tool_inventory(self) -> list[dict[str, object]]:
            return []

    HarnessMemoryService = importlib.import_module("app.harness.memory").HarnessMemoryService
    HarnessPromptAssembler = importlib.import_module("app.harness.prompts").HarnessPromptAssembler

    session_response = client.post("/api/sessions", json={"title": "Context Window Session"})
    session_id = api_data(session_response)["id"]

    with DBSession(app.state.database_engine) as db_session:
        repository = SessionRepository(db_session)
        session = repository.get_session(session_id)
        assert session is not None
        active_branch = repository.ensure_active_branch(session)
        sequence = 1
        turn_index = 1
        for index in range(12):
            user_message = repository.create_message(
                session=session,
                role=MessageRole.USER,
                content=f"user turn {index} " + ("A" * 320),
                attachments=[],
                branch_id=active_branch.id,
                sequence=sequence,
                turn_index=turn_index,
            )
            sequence += 1
            repository.create_message(
                session=session,
                role=MessageRole.ASSISTANT,
                content=f"assistant turn {index} " + ("B" * 320),
                attachments=[],
                parent_message_id=user_message.id,
                branch_id=active_branch.id,
                status=MessageStatus.COMPLETED,
                sequence=sequence,
                turn_index=turn_index,
                metadata_json=(
                    {
                        "semantic_state": {
                            "active_hypotheses": ["hypothesis:manual-restore"],
                            "evidence_ids": ["runtime:manual-restore"],
                            "graph_hints": [
                                {
                                    "graph_type": "attack",
                                    "stable_key": "runtime:manual-restore",
                                }
                            ],
                            "artifacts": ["reports/manual-restore.txt"],
                            "recent_entities": ["restore.example.internal"],
                            "recent_tools": ["execute_kali_command"],
                            "reason": "Persist compacted semantic state.",
                        }
                    }
                    if index == 3
                    else None
                ),
            )
            sequence += 1
            turn_index += 1
        existing_events = repository.list_session_events(session_id, limit=2_000)
        max_cursor = max(
            (event.cursor for event in existing_events if isinstance(event.cursor, int)),
            default=0,
        )

    context_window_response = client.get(f"/api/sessions/{session_id}/context-window")
    assert context_window_response.status_code == 200
    context_window_payload = api_data(context_window_response)
    assert context_window_payload["session_id"] == session_id
    assert context_window_payload["can_manual_compact"] is True
    assert context_window_payload["used_tokens"] > 0
    assert context_window_payload["last_compacted_at"] is None

    with client.websocket_connect(
        f"/api/sessions/{session_id}/events?cursor={max_cursor}"
    ) as websocket:
        compact_response = client.post(
            f"/api/sessions/{session_id}/compact",
            json={"mode": "manual"},
        )
        assert compact_response.status_code == 200
        compact_payload = api_data(compact_response)
        assert compact_payload["session_id"] == session_id
        assert compact_payload["mode"] == "manual"
        assert compact_payload["compacted"] is True
        assert compact_payload["summary"] == "已压缩对话"
        assert compact_payload["reclaimed_tokens"] > 0
        assert str(compact_payload["compact_boundary"]).startswith("compact-boundary:")

        events: list[dict[str, object]] = []
        seen_types: set[str] = set()
        while "session.context_window.updated" not in seen_types:
            event = websocket.receive_json()
            events.append(event)
            seen_types.add(str(event["type"]))

    event_types = [str(event["type"]) for event in events]
    assert "session.compaction.completed" in event_types
    assert "session.context_window.updated" in event_types
    typed_event_cursors = cast(
        list[int],
        [event["cursor"] for event in events if isinstance(event.get("cursor"), int)],
    )
    assert all(cursor > max_cursor for cursor in typed_event_cursors)

    updated_context_window = api_data(client.get(f"/api/sessions/{session_id}/context-window"))
    assert updated_context_window["last_compact_boundary"] == compact_payload["compact_boundary"]
    assert updated_context_window["last_compacted_at"] == compact_payload["created_at"]

    with DBSession(app.state.database_engine) as db_session:
        repository = SessionRepository(db_session)
        session = repository.get_session(session_id)
        assert session is not None
        active_branch = repository.ensure_active_branch(session)
        visible_messages = repository.list_messages(
            session_id,
            branch_id=active_branch.id,
            include_superseded=False,
        )
        compacted_history_message = next(
            message for message in visible_messages if "## Compacted History" in message.content
        )
        assert compacted_history_message.role == MessageRole.USER
        assert compacted_history_message.metadata_json["summary"] == "已压缩对话"
        assert (
            compacted_history_message.metadata_json["compact_boundary"]
            == compact_payload["compact_boundary"]
        )
        persisted_compaction_state = cast(
            dict[str, object], compacted_history_message.metadata_json["compaction_state"]
        )
        assert persisted_compaction_state["mode"] == "full"
        assert (
            persisted_compaction_state["active_compact_fragment"]
            == compacted_history_message.content
        )
        assert isinstance(persisted_compaction_state["durable_artifact_ref"], str)
        persisted_semantic_state = cast(
            dict[str, object], compacted_history_message.metadata_json["semantic_state"]
        )
        assert persisted_semantic_state["active_hypotheses"] == ["hypothesis:manual-restore"]
        assert persisted_semantic_state["recent_entities"] == ["restore.example.internal"]
        assert not any(
            message.role == MessageRole.ASSISTANT
            and message.content.startswith("assistant turn 3 ")
            for message in visible_messages
        )

        rebuilt_history = repository.build_conversation_context(
            session_id=session.id,
            branch_id=active_branch.id,
            rough_token_budget=12_000,
        )
        assert any("## Compacted History" in message.content for message in rebuilt_history)
        assert not any(
            message.role == MessageRole.ASSISTANT
            and message.content.startswith("assistant turn 3 ")
            for message in rebuilt_history
        )

        latest_assistant = next(
            message
            for message in reversed(visible_messages)
            if message.role == MessageRole.ASSISTANT
        )
        latest_user = next(
            message for message in reversed(visible_messages) if message.role == MessageRole.USER
        )
        assembler = HarnessPromptAssembler(
            capability_facade=_FakeCapabilityFacade(),
            skill_service=_FakeSkillService(),
            memory_service=HarnessMemoryService(),
        )
        assembly = assembler.build(
            session=session,
            repository=repository,
            user_message=latest_user,
            assistant_message=latest_assistant,
            branch_id=active_branch.id,
            total_token_budget=12_000,
        )

    assert any(
        conversation_message.content.startswith("## Compacted History")
        for conversation_message in assembly.conversation_messages
    )
    assert assembly.session_state.semantic.active_hypotheses == ["hypothesis:manual-restore"]
    assert assembly.session_state.semantic.evidence_ids == ["runtime:manual-restore"]
    assert assembly.session_state.semantic.recent_entities == ["restore.example.internal"]
    assert assembly.session_state.compaction.mode == "full"
    assert assembly.session_state.compaction.active_compact_fragment.startswith(
        "## Compacted History"
    )
    assert (
        assembly.session_state.compaction.durable_artifact_ref
        == persisted_compaction_state["durable_artifact_ref"]
    )

    edit_response = client.post(
        f"/api/sessions/{session_id}/messages/{compacted_history_message.id}/edit",
        json={"content": "tamper", "attachments": []},
    )
    assert edit_response.status_code == 409
    assert edit_response.json()["detail"] == "Compacted history messages cannot be edited."


def test_publish_auto_compaction_events_persists_trace_and_context_window(
    client: TestClient,
) -> None:
    session_response = client.post("/api/sessions", json={"title": "Auto Compact Session"})
    session_id = api_data(session_response)["id"]

    with DBSession(app.state.database_engine) as db_session:
        repository = SessionRepository(db_session)
        session = repository.get_session(session_id)
        assert session is not None
        active_branch = repository.ensure_active_branch(session)
        user_message = repository.create_message(
            session=session,
            role=MessageRole.USER,
            content="请继续分析",
            attachments=[],
            branch_id=active_branch.id,
            sequence=1,
            turn_index=1,
        )
        assistant_message = repository.create_message(
            session=session,
            role=MessageRole.ASSISTANT,
            content="已有阶段性结论。",
            attachments=[],
            parent_message_id=user_message.id,
            branch_id=active_branch.id,
            status=MessageStatus.COMPLETED,
            sequence=2,
            turn_index=1,
        )

        event_broker_factory = app.dependency_overrides[get_event_broker]
        assert event_broker_factory is not None
        event_broker = event_broker_factory()

        published = asyncio.run(
            _publish_auto_compaction_events_if_needed(
                repository=repository,
                event_broker=event_broker,
                session=session,
                assistant_message=assistant_message,
                prompt_assembly=SimpleNamespace(
                    prompt_budget=SimpleNamespace(
                        component_tokens={
                            "core_immutable": 10,
                            "safety_scope": 10,
                            "role_prompt": 0,
                            "capability_schema": 4,
                            "capability_prompt": 18,
                            "task_local": 12,
                            "history": 16,
                        }
                    ),
                    memory_context=SimpleNamespace(
                        retrieval_fragment="retrieval fragment",
                        memory_fragment="memory fragment",
                    ),
                ),
                session_state=SimpleNamespace(
                    compaction=SimpleNamespace(mode="full", last_compacted_turn=9)
                ),
                initial_compaction_turn=0,
            )
        )

        assert published is True

        events = [
            {
                "type": event.event_type,
                "payload": dict(event.payload_json),
                "cursor": event.cursor,
            }
            for event in repository.list_session_events(session_id, limit=2_000)
        ]

    trace_event = next(
        event
        for event in events
        if event["type"] == "assistant.trace"
        and isinstance(event.get("payload"), dict)
        and cast(dict[str, object], event["payload"]).get("state") == "context.compacted"
    )
    trace_payload = cast(dict[str, object], trace_event["payload"])
    assert trace_payload["summary"] == "已压缩对话"

    compaction_event = next(
        event for event in events if event["type"] == "session.compaction.completed"
    )
    compaction_payload = cast(dict[str, object], compaction_event["payload"])
    assert compaction_payload["mode"] == "automatic"
    assert compaction_payload["summary"] == "已压缩对话"

    context_window_event = next(
        event for event in events if event["type"] == "session.context_window.updated"
    )
    context_window_payload = cast(dict[str, object], context_window_event["payload"])
    assert context_window_payload["last_compact_boundary"] == trace_payload["compact_boundary"]
    assert context_window_payload["can_manual_compact"] is False

    conversation_payload = api_data(client.get(f"/api/sessions/{session_id}/conversation"))
    latest_assistant = next(
        message
        for message in reversed(conversation_payload["messages"])
        if message["role"] == "assistant"
    )
    assert any(
        segment["kind"] == "status" and segment["text"] == "已压缩对话"
        for segment in latest_assistant["assistant_transcript"]
    )


def test_websocket_rejects_invalid_session_before_accept(client: TestClient) -> None:
    with pytest.raises(WebSocketDenialResponse) as exc_info:
        with client.websocket_connect("/api/sessions/nonexistent-session/events"):
            pass

    response = exc_info.value
    assert response.status_code == 404
    assert "Session not found" in response.text


def test_session_compact_rejects_active_generation_and_publishes_failed_event(
    client: TestClient,
) -> None:
    class BlockingChatRuntime:
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
            del (
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                execute_tool,
            )
            assert callbacks is not None
            assert callbacks.on_text_delta is not None
            await callbacks.on_text_delta("running")
            await asyncio.sleep(0.5)
            return f"done: {content}"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: BlockingChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Compact Reject Session"})
        session_id = api_data(session_response)["id"]

        with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
            chat_response_box, worker = _post_chat_in_thread(
                client,
                session_id,
                {"content": "hold", "attachments": [], "wait_for_completion": True},
            )

            while True:
                event = websocket.receive_json()
                if event["type"] == "generation.started":
                    break

            compact_response = client.post(
                f"/api/sessions/{session_id}/compact",
                json={"mode": "manual"},
            )
            assert compact_response.status_code == 409
            assert compact_response.json()["detail"] == {
                "mode": "manual",
                "summary": "上下文压缩失败",
                "error": "active generation is running",
            }

            while True:
                event = websocket.receive_json()
                if event["type"] == "session.compaction.failed":
                    failed_event = event
                    break

            cancel_response = client.post(f"/api/sessions/{session_id}/cancel")
            assert cancel_response.status_code == 200

        worker.join(timeout=5)
        assert chat_response_box["value"] is not None
        assert chat_response_box["value"].status_code == 409
        assert failed_event["payload"] == {
            "mode": "manual",
            "summary": "上下文压缩失败",
            "error": "active generation is running",
        }
        assert isinstance(failed_event.get("cursor"), int)
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_cancel_session_interrupts_active_generation(client: TestClient) -> None:
    class SlowStreamingChatRuntime:
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
            del (
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                execute_tool,
            )
            assert callbacks is not None
            assert callbacks.on_text_delta is not None
            for chunk in ["partial ", "response ", f"for {content}"]:
                if callbacks.is_cancelled is not None and callbacks.is_cancelled():
                    raise asyncio.CancelledError
                await callbacks.on_text_delta(chunk)
                await asyncio.sleep(0.05)
            return f"partial response for {content}"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: SlowStreamingChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Cancelable Session"})
        session_id = api_data(session_response)["id"]

        with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
            chat_response_box, worker = _post_chat_in_thread(
                client,
                session_id,
                {"content": "cancel me", "attachments": [], "wait_for_completion": True},
            )

            saw_partial_update = False
            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "message.updated":
                    saw_partial_update = True
                    cancel_response = client.post(f"/api/sessions/{session_id}/cancel")
                    assert cancel_response.status_code == 200
                if event["type"] == "generation.cancelled":
                    break

        worker.join(timeout=5)
        chat_response = chat_response_box["value"]
        assert chat_response is not None
        assert chat_response.status_code == 409
        assert chat_response.json()["detail"] == "Active generation was cancelled."
        assert saw_partial_update is True
        event_types = [event["type"] for event in events]
        assert "generation.started" in event_types
        assert "generation.cancelled" in event_types
        graph_updates = [event for event in events if event["type"] == "graph.updated"]
        assert graph_updates
        assert any(event["payload"].get("graph_type") == "attack" for event in graph_updates)

        detail_response = client.get(f"/api/sessions/{session_id}")
        detail_payload = api_data(detail_response)
        assert detail_payload["status"] == "cancelled"
        assert detail_payload["messages"][1]["content"].startswith("partial")
        assert detail_payload["messages"][1]["content"] != "partial response for cancel me"
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_queues_follow_up_prompt_while_generation_is_active(client: TestClient) -> None:
    second_request_started = threading.Event()

    class OrderedStreamingChatRuntime:
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
            del (
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                execute_tool,
            )
            assert callbacks is not None
            assert callbacks.on_text_delta is not None
            for chunk in [f"reply[{content}]", " done"]:
                await callbacks.on_text_delta(chunk)
                if content == "first":
                    second_request_started.wait(timeout=1)
                await _yield_control()
            return f"reply[{content}] done"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: OrderedStreamingChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Queued Session"})
        session_id = api_data(session_response)["id"]

        with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
            first_response_box, first_worker = _post_chat_in_thread(
                client,
                session_id,
                {"content": "first", "attachments": [], "wait_for_completion": True},
            )

            first_generation_started = False
            second_generation_started = False
            second_response_box: dict[str, Response | None] | None = None
            second_worker = None
            events = []

            while True:
                event = websocket.receive_json()
                events.append(event)

                if (
                    event["type"] == "generation.started"
                    and event["payload"]["queued_prompt_count"] == 0
                    and not first_generation_started
                ):
                    first_generation_started = True
                    second_response_box, second_worker = _post_chat_in_thread(
                        client,
                        session_id,
                        {"content": "second", "attachments": [], "wait_for_completion": True},
                    )
                    second_request_started.set()
                    continue

                if (
                    event["type"] == "generation.started"
                    and event["payload"]["queued_prompt_count"] == 0
                    and first_generation_started
                ):
                    second_generation_started = True

                if second_generation_started and event["type"] == "session.updated":
                    if event["payload"].get("status") == "done":
                        break

        first_worker.join(timeout=5)
        first_response = first_response_box["value"]
        assert first_response is not None
        assert first_response.status_code == 200
        assert api_data(first_response)["assistant_message"]["content"] == "reply[first] done"

        assert second_worker is not None
        assert second_response_box is not None
        second_worker.join(timeout=5)
        second_response = second_response_box["value"]
        assert second_response is not None
        assert second_response.status_code == 200
        assert api_data(second_response)["assistant_message"]["content"] == "reply[second] done"

        generation_events = [event for event in events if event["type"] == "generation.started"]
        assert len(generation_events) == 2
        assert generation_events[0]["payload"]["queued_prompt_count"] == 0
        assert generation_events[1]["payload"]["queued_prompt_count"] == 0
        assert second_generation_started is True
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_websocket_releases_db_session_after_initial_lookup(
    client: TestClient, monkeypatch: MonkeyPatch
) -> None:
    class TrackingSession:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    tracking_session = TrackingSession()
    original_override = app.dependency_overrides[get_websocket_db_session]

    def override_db_session() -> TrackingSession:
        return tracking_session

    def fake_get_session(
        self: SessionRepository, session_id: str, *, include_deleted: bool = False
    ) -> object:
        del self, session_id, include_deleted
        return object()

    app.dependency_overrides[get_websocket_db_session] = override_db_session
    monkeypatch.setattr(SessionRepository, "get_session", fake_get_session)

    try:
        with ExitStack() as stack:
            stack.enter_context(client.websocket_connect("/api/sessions/test-session/events"))
            assert tracking_session.close_calls == 1
    finally:
        app.dependency_overrides[get_websocket_db_session] = original_override


def test_chat_can_auto_call_runtime_tools(client: TestClient) -> None:
    class ToolCallingChatRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                callbacks,
            )
            assert execute_tool is not None
            tool_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="tool-call-1",
                    tool_name="execute_kali_command",
                    arguments={
                        "command": "printf 'auto tool' > reports/auto.txt",
                        "timeout_seconds": 10,
                        "artifact_paths": ["reports/auto.txt"],
                    },
                )
            )
            return f"工具执行完成，状态：{tool_result.payload['status']}。"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: ToolCallingChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Auto Tool Session"})
        session_id = api_data(session_response)["id"]

        with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
            chat_response = client.post(
                f"/api/sessions/{session_id}/chat",
                json={"content": "请自动执行工具", "attachments": [], "wait_for_completion": True},
            )

            assert chat_response.status_code == 200
            assert (
                api_data(chat_response)["assistant_message"]["content"]
                == "工具执行完成，状态：success。"
            )

            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)

                if event["type"] == "session.updated" and event["payload"].get("status") == "done":
                    break

        event_types = [event["type"] for event in events]
        assert event_types[:4] == [
            "session.updated",
            "message.created",
            "message.created",
            "generation.started",
        ]
        assert event_types[-1] == "session.updated"
        assert "assistant.summary" not in event_types
        assert "assistant.trace" in event_types
        assert "tool.call.started" in event_types
        assert "tool.call.finished" in event_types
        assert "message.delta" in event_types
        assert "message.updated" in event_types
        assert "message.completed" in event_types

        started_index = event_types.index("tool.call.started")
        finished_index = event_types.index("tool.call.finished")
        assistant_created_index = event_types.index("message.created", 2)
        last_message_update_index = max(
            index for index, event_type in enumerate(event_types) if event_type == "message.updated"
        )

        started_payload = events[started_index]["payload"]
        assert started_payload["tool"] == "execute_kali_command"
        assert started_payload["tool_call_id"] == "tool-call-1"
        assert started_payload["arguments"] == {
            "command": "printf 'auto tool' > reports/auto.txt",
            "timeout_seconds": 10,
            "artifact_paths": ["reports/auto.txt"],
        }
        assert started_payload["command"] == "printf 'auto tool' > reports/auto.txt"
        assert started_payload["timeout_seconds"] == 10
        assert started_payload["artifact_paths"] == ["reports/auto.txt"]
        assert started_payload["message_id"] == api_data(chat_response)["assistant_message"]["id"]
        assert (
            started_payload["assistant_message_id"]
            == api_data(chat_response)["assistant_message"]["id"]
        )
        assert started_payload["generation_id"] == api_data(chat_response)["generation"]["id"]
        assert started_payload["risk_level"] == "high"
        assert started_payload["mutating_target_class"] == "runtime"
        assert started_payload["command_summary"] == "printf 'auto tool' > reports/auto.txt"
        assert events[finished_index]["payload"]["tool"] == "execute_kali_command"
        assert events[finished_index]["payload"]["tool_call_id"] == "tool-call-1"
        assert (
            events[finished_index]["payload"]["message_id"]
            == api_data(chat_response)["assistant_message"]["id"]
        )
        assert (
            events[finished_index]["payload"]["assistant_message_id"]
            == api_data(chat_response)["assistant_message"]["id"]
        )
        assert (
            events[finished_index]["payload"]["generation_id"]
            == api_data(chat_response)["generation"]["id"]
        )
        assert isinstance(events[finished_index]["payload"].get("run_id"), str)
        assert isinstance(events[finished_index]["payload"].get("created_at"), str)
        assert (
            events[finished_index]["payload"]["command"] == "printf 'auto tool' > reports/auto.txt"
        )
        assert events[finished_index]["payload"]["status"] == "success"
        assert events[finished_index]["payload"]["exit_code"] == 0
        assert events[finished_index]["payload"]["requested_timeout_seconds"] == 10
        assert events[finished_index]["payload"]["stdout"] == "runtime command completed"
        assert events[finished_index]["payload"]["stderr"] == ""
        assert events[finished_index]["payload"]["artifact_paths"] == ["reports/auto.txt"]
        assert events[finished_index]["payload"]["evidence_ids"] == [
            f"runtime:{events[finished_index]['payload']['run_id']}"
        ]
        assert events[finished_index]["payload"]["hypothesis_ids"] == []
        assert events[finished_index]["payload"]["artifacts"] == ["reports/auto.txt"]
        assert events[finished_index]["payload"]["reason"] == (
            "Runtime command completed with status success."
        )
        assert events[finished_index]["payload"]["graph_updates"][0]["stable_key"] == (
            f"runtime:{events[finished_index]['payload']['run_id']}"
        )
        assert events[finished_index]["payload"]["result"] == {
            "command": "printf 'auto tool' > reports/auto.txt",
            "status": "success",
            "exit_code": 0,
            "stdout": "runtime command completed",
            "stderr": "",
            "artifacts": ["reports/auto.txt"],
        }
        assert events[assistant_created_index]["payload"]["role"] == "assistant"
        assert events[assistant_created_index]["payload"]["content"] == ""
        assert (
            events[last_message_update_index]["payload"]["content"]
            == "工具执行完成，状态：success。"
        )
        tool_update_payloads = [
            event["payload"]
            for event in events
            if event["type"] == "message.updated"
            and isinstance(event["payload"].get("assistant_transcript"), list)
        ]
        assert tool_update_payloads
        graph_update_payloads = [
            event["payload"] for event in events if event["type"] == "graph.updated"
        ]
        assert graph_update_payloads
        assert any(
            payload.get("graph_type") == "attack"
            and payload.get("assistant_message_id")
            == api_data(chat_response)["assistant_message"]["id"]
            for payload in graph_update_payloads
        )
        assert any(
            started_index < index < finished_index
            for index, event in enumerate(events)
            if event["type"] == "graph.updated"
        )
        assert any(
            any(segment["kind"] == "tool_call" for segment in payload["assistant_transcript"])
            for payload in tool_update_payloads
        )
        assert any(
            any(segment["kind"] == "tool_result" for segment in payload["assistant_transcript"])
            for payload in tool_update_payloads
        )
        trace_payloads = [
            event["payload"] for event in events if event["type"] == "assistant.trace"
        ]
        assert any(
            payload.get("state") == "tool.finished"
            and payload.get("semantic_state", {}).get("evidence_ids")
            == [f"runtime:{events[finished_index]['payload']['run_id']}"]
            for payload in trace_payloads
        )

        runtime_status_response = client.get("/api/runtime/status")
        assert runtime_status_response.status_code == 200
        runtime_payload = api_data(runtime_status_response)
        assert runtime_payload["recent_runs"][0]["session_id"] == session_id
        assert (
            runtime_payload["recent_runs"][0]["artifacts"][0]["relative_path"] == "reports/auto.txt"
        )
        steps = api_data(chat_response)["generation"]["steps"]
        assert any(
            step["kind"] == "tool" and step["tool_call_id"] == "tool-call-1" for step in steps
        )
        assert any(
            step["kind"] == "output" and "工具执行完成" in step["delta_text"] for step in steps
        )
        transcript = api_data(chat_response)["assistant_message"]["assistant_transcript"]
        assert [segment["sequence"] for segment in transcript] == list(
            range(1, len(transcript) + 1)
        )
        tool_call_segment = next(
            segment
            for segment in transcript
            if segment["kind"] == "tool_call" and segment["tool_call_id"] == "tool-call-1"
        )
        tool_result_segment = next(
            segment
            for segment in transcript
            if segment["kind"] == "tool_result" and segment["tool_call_id"] == "tool-call-1"
        )
        output_segment = next(segment for segment in transcript if segment["kind"] == "output")
        assert tool_call_segment["status"] == "completed"
        assert tool_result_segment["metadata"]["stdout"] == "runtime command completed"
        assert tool_result_segment["metadata"]["stderr"] == ""
        assert tool_result_segment["metadata"]["artifacts"] == ["reports/auto.txt"]
        assert tool_result_segment["metadata"]["result"] == {
            "command": "printf 'auto tool' > reports/auto.txt",
            "status": "success",
            "exit_code": 0,
            "stdout": "runtime command completed",
            "stderr": "",
            "artifacts": ["reports/auto.txt"],
        }
        assert output_segment["text"] == "工具执行完成，状态：success。"
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_session_slash_catalog_uses_governed_builtin_skill_and_mcp_sources(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_skills(
        client,
        monkeypatch,
        tmp_path,
        {
            "adscan": """---
name: adscan
description: Active Directory 枚举 skill
user-invocable: true
compatibility: [opencode]
---
# adscan

Use when performing Active Directory pentest orchestration.
""",
        },
    )

    class FakeMCPService:
        def list_servers(self) -> list[MCPServerRead]:
            return [
                MCPServerRead(
                    id="server-1",
                    name="Burp Suite",
                    source=CompatibilitySource.LOCAL,
                    scope=CompatibilityScope.PROJECT,
                    transport=MCPTransport.STDIO,
                    enabled=True,
                    timeout_ms=30_000,
                    status=MCPServerStatus.CONNECTED,
                    config_path="mcp.json",
                    imported_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
                    capabilities=[
                        MCPCapabilityRead(
                            kind=MCPCapabilityKind.TOOL,
                            name="scan-target",
                            title="Scan Target",
                            description="Run a focused MCP scan.",
                            input_schema={
                                "type": "object",
                                "properties": {"target": {"type": "string"}},
                                "required": ["target"],
                                "additionalProperties": False,
                            },
                        )
                    ],
                )
            ]

    original_mcp_override = app.dependency_overrides.get(get_mcp_service)
    app.dependency_overrides[get_mcp_service] = lambda: FakeMCPService()

    try:
        session_response = client.post("/api/sessions", json={"title": "Slash Catalog Session"})
        session_id = api_data(session_response)["id"]

        catalog_response = client.get(f"/api/sessions/{session_id}/slash-catalog")

        assert catalog_response.status_code == 200
        catalog = api_data(catalog_response)
        assert any(item["id"] == "builtin:list_available_skills" for item in catalog)
        assert any(item["id"] == "skill:adscan" for item in catalog)
        assert any(item["id"] == "mcp:server-1:scan-target" for item in catalog)

        builtin_item = next(
            item for item in catalog if item["id"] == "builtin:list_available_skills"
        )
        assert builtin_item["action"] == {
            "id": "builtin:list_available_skills",
            "trigger": "list-available-skills",
            "type": "builtin",
            "source": "builtin",
            "display_text": "/list-available-skills",
            "invocation": {
                "tool_name": "list_available_skills",
                "arguments": {},
                "mcp_server_id": None,
                "mcp_tool_name": None,
            },
        }
        disabled_builtin = next(
            item for item in catalog if item["id"] == "builtin:execute_kali_command"
        )
        assert disabled_builtin["disabled"] is True

        skill_item = next(item for item in catalog if item["id"] == "skill:adscan")
        assert skill_item["action"]["invocation"] == {
            "tool_name": "execute_skill",
            "arguments": {"skill_name_or_id": "adscan"},
            "mcp_server_id": None,
            "mcp_tool_name": None,
        }

        mcp_item = next(item for item in catalog if item["id"] == "mcp:server-1:scan-target")
        assert mcp_item["badge"] == "Burp Suite"
        assert mcp_item["disabled"] is True
        assert mcp_item["action"]["invocation"] == {
            "tool_name": "mcp__burp_suite__scan_target",
            "arguments": {},
            "mcp_server_id": "server-1",
            "mcp_tool_name": "scan-target",
        }
    finally:
        if original_mcp_override is None:
            app.dependency_overrides.pop(get_mcp_service, None)
        else:
            app.dependency_overrides[get_mcp_service] = original_mcp_override


def test_session_slash_catalog_uses_full_user_invocable_skill_inventory(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_skills(
        client,
        monkeypatch,
        tmp_path,
        {
            "container-security": """---
name: Container Security
description: Container hardening skill
user-invocable: true
compatibility: [opencode]
---
# Container Security

Use when reviewing container security posture.
""",
            "ctf-crypto": """---
name: CTF Crypto
description: CTF crypto skill
user-invocable: true
compatibility: [opencode]
---
# CTF Crypto

Use for crypto challenges.
""",
            "hidden-helper": """---
name: Hidden Helper
description: Internal helper skill
user-invocable: false
compatibility: [opencode]
---
# Hidden Helper

Internal helper only.
""",
            "disabled-skill": """---
name: Disabled Skill
description: Disabled skill
user-invocable: true
compatibility: [opencode]
---
# Disabled Skill

Should not appear when disabled.
""",
            "static-analysis/skills/semgrep": """---
name: Semgrep Static
description: Static semgrep skill
user-invocable: true
compatibility: [opencode]
---
# Semgrep Static

Use for static analysis semgrep tasks.
""",
            "dynamic-analysis/skills/semgrep": """---
name: Semgrep Dynamic
description: Dynamic semgrep skill
user-invocable: true
compatibility: [opencode]
---
# Semgrep Dynamic

Use for dynamic analysis semgrep tasks.
""",
        },
    )

    skill_records = api_data(client.get("/api/skills"))
    disabled_skill = next(
        record for record in skill_records if record["directory_name"] == "disabled-skill"
    )
    disable_response = client.post(f"/api/skills/{disabled_skill['id']}/disable")
    assert disable_response.status_code == 200

    session_response = client.post(
        "/api/sessions", json={"title": "Full Skill Slash Catalog Session"}
    )
    session_id = api_data(session_response)["id"]

    catalog_response = client.get(f"/api/sessions/{session_id}/slash-catalog")

    assert catalog_response.status_code == 200
    catalog = api_data(catalog_response)
    catalog_ids = {item["id"] for item in catalog}

    assert "skill:container-security" in catalog_ids
    assert "skill:ctf-crypto" in catalog_ids
    assert "skill:dynamic-analysis/semgrep" in catalog_ids
    assert "skill:hidden-helper" not in catalog_ids
    assert "skill:static-analysis/semgrep" in catalog_ids
    assert "skill:disabled-skill" not in catalog_ids
    assert "skill:semgrep" not in catalog_ids


def test_chat_rejects_invalid_stale_slash_action_payload(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Stale Slash Session"})
    session_id = api_data(session_response)["id"]
    catalog = api_data(client.get(f"/api/sessions/{session_id}/slash-catalog"))
    builtin_item = next(item for item in catalog if item["id"] == "builtin:list_available_skills")
    stale_action = dict(builtin_item["action"])
    stale_action["trigger"] = "stale-trigger"

    chat_response = client.post(
        f"/api/sessions/{session_id}/chat",
        json={
            "content": builtin_item["action"]["display_text"],
            "attachments": [],
            "wait_for_completion": True,
            "slash_action": stale_action,
        },
    )

    assert chat_response.status_code == 422
    assert "stale slash_action" in chat_response.json()["detail"]


def test_chat_rejects_slash_action_id_with_extra_path_suffix(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Slash Path Session"})
    session_id = api_data(session_response)["id"]
    catalog = api_data(client.get(f"/api/sessions/{session_id}/slash-catalog"))
    builtin_item = next(item for item in catalog if item["id"] == "builtin:list_available_skills")
    malformed_action = dict(builtin_item["action"])
    malformed_action["id"] = f"{builtin_item['action']['id']}/server-side-exec"

    chat_response = client.post(
        f"/api/sessions/{session_id}/chat",
        json={
            "content": builtin_item["action"]["display_text"],
            "attachments": [],
            "wait_for_completion": True,
            "slash_action": malformed_action,
        },
    )

    assert chat_response.status_code == 422
    assert "stale slash_action" in chat_response.json()["detail"]


def test_chat_structured_slash_action_executes_governed_tool_with_openai_history(
    client: TestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    settings = app.state.settings
    original_openai_config = (
        settings.llm_api_key,
        settings.llm_api_base_url,
        settings.llm_default_model,
    )
    settings.llm_api_key = "test-openai-key"
    settings.llm_api_base_url = "https://example.test/openai"
    settings.llm_default_model = "gpt-test"

    runtime = OpenAICompatibleChatRuntime(settings)
    captured_payloads: list[dict[str, object]] = []

    async def fake_stream_completion(
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, object],
        callbacks: GenerationCallbacks,
    ) -> dict[str, object]:
        del endpoint, headers
        captured_payloads.append(payload)
        messages = payload["messages"]
        assert isinstance(messages, list)
        assistant_index, assistant_message = next(
            (index, item)
            for index, item in enumerate(messages)
            if isinstance(item, dict) and item.get("role") == "assistant" and item.get("tool_calls")
        )
        tool_index, tool_message = next(
            (index, item)
            for index, item in enumerate(messages)
            if isinstance(item, dict) and item.get("role") == "tool"
        )
        assert assistant_index < tool_index
        tool_call = assistant_message["tool_calls"][0]
        assert tool_call["function"]["name"] == "list_available_skills"
        assert tool_call["type"] == "function"
        assert json.loads(tool_call["function"]["arguments"]) == {}
        assert tool_message["tool_call_id"] == tool_call["id"]
        assert tool_message["name"] == tool_call["function"]["name"]
        assert tool_message["content"]
        assert callbacks.on_text_delta is not None
        await callbacks.on_text_delta("slash openai ok")
        return {"choices": [{"message": {"role": "assistant", "content": "slash openai ok"}}]}

    monkeypatch.setattr(runtime, "_stream_completion", fake_stream_completion)
    original_runtime_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: runtime

    try:
        session_response = client.post("/api/sessions", json={"title": "OpenAI Slash Session"})
        session_id = api_data(session_response)["id"]
        catalog = api_data(client.get(f"/api/sessions/{session_id}/slash-catalog"))
        builtin_item = next(
            item for item in catalog if item["id"] == "builtin:list_available_skills"
        )

        with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
            chat_response = client.post(
                f"/api/sessions/{session_id}/chat",
                json={
                    "content": builtin_item["action"]["display_text"],
                    "attachments": [],
                    "wait_for_completion": True,
                    "slash_action": builtin_item["action"],
                },
            )

            assert chat_response.status_code == 200
            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "session.updated" and event["payload"].get("status") == "done":
                    break

        chat_payload = api_data(chat_response)
        assert chat_payload["assistant_message"]["content"] == "slash openai ok"
        assert (
            chat_payload["user_message"]["metadata"]["slash_action"]["id"]
            == "builtin:list_available_skills"
        )
        assert chat_payload["generation"]["metadata"]["slash_action"]["source"] == "builtin"
        transcript = chat_payload["assistant_message"]["assistant_transcript"]
        tool_call_segment = next(
            segment
            for segment in transcript
            if segment["kind"] == "tool_call" and segment["tool_name"] == "list_available_skills"
        )
        tool_result_segment = next(
            segment
            for segment in transcript
            if segment["kind"] == "tool_result"
            and segment["tool_call_id"] == tool_call_segment["tool_call_id"]
        )
        assert isinstance(tool_result_segment["metadata"]["result"]["skills"], list)
        event_types = [event["type"] for event in events]
        assert "tool.call.started" in event_types
        assert "tool.call.finished" in event_types
        assert captured_payloads
    finally:
        settings.llm_api_key, settings.llm_api_base_url, settings.llm_default_model = (
            original_openai_config
        )
        app.dependency_overrides[get_chat_runtime] = original_runtime_override


def test_chat_structured_slash_action_keeps_anthropic_tool_history_valid(
    client: TestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    settings = app.state.settings
    original_anthropic_config = (
        settings.anthropic_api_key,
        settings.anthropic_api_base_url,
        settings.anthropic_model,
    )
    settings.anthropic_api_key = "test-anthropic-key"
    settings.anthropic_api_base_url = "https://example.test/anthropic"
    settings.anthropic_model = "claude-test"

    runtime = AnthropicChatRuntime(settings)
    captured_payloads: list[dict[str, object]] = []

    async def fake_stream_completion(
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, object],
        callbacks: GenerationCallbacks,
    ) -> dict[str, object]:
        del endpoint, headers
        captured_payloads.append(payload)
        messages = payload["messages"]
        assert isinstance(messages, list)
        assistant_index, assistant_message = next(
            (index, item)
            for index, item in enumerate(messages)
            if isinstance(item, dict)
            and item.get("role") == "assistant"
            and isinstance(item.get("content"), list)
            and any(
                block.get("type") == "tool_use"
                for block in item["content"]
                if isinstance(block, dict)
            )
        )
        user_index, user_message = next(
            (index, item)
            for index, item in enumerate(messages)
            if isinstance(item, dict)
            and item.get("role") == "user"
            and isinstance(item.get("content"), list)
            and any(
                block.get("type") == "tool_result"
                for block in item["content"]
                if isinstance(block, dict)
            )
        )
        assert assistant_index < user_index
        tool_use_block = next(
            block for block in assistant_message["content"] if block["type"] == "tool_use"
        )
        tool_result_block = next(
            block for block in user_message["content"] if block["type"] == "tool_result"
        )
        assert tool_use_block["name"] == "list_available_skills"
        assert tool_use_block["input"] == {}
        assert tool_result_block["tool_use_id"] == tool_use_block["id"]
        assert callbacks.on_text_delta is not None
        await callbacks.on_text_delta("slash anthropic ok")
        return {"content": [{"type": "text", "text": "slash anthropic ok"}]}

    monkeypatch.setattr(runtime, "_stream_completion", fake_stream_completion)
    original_runtime_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: runtime

    try:
        session_response = client.post("/api/sessions", json={"title": "Anthropic Slash Session"})
        session_id = api_data(session_response)["id"]
        catalog = api_data(client.get(f"/api/sessions/{session_id}/slash-catalog"))
        builtin_item = next(
            item for item in catalog if item["id"] == "builtin:list_available_skills"
        )

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={
                "content": builtin_item["action"]["display_text"],
                "attachments": [],
                "wait_for_completion": True,
                "slash_action": builtin_item["action"],
            },
        )

        assert chat_response.status_code == 200
        assert api_data(chat_response)["assistant_message"]["content"] == "slash anthropic ok"
        assert captured_payloads
    finally:
        (
            settings.anthropic_api_key,
            settings.anthropic_api_base_url,
            settings.anthropic_model,
        ) = original_anthropic_config
        app.dependency_overrides[get_chat_runtime] = original_runtime_override


def test_chat_readonly_parallel_batch_execution_preserves_order(client: TestClient) -> None:
    class ParallelReadonlyChatRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                callbacks,
            )
            assert execute_tool is not None
            batch_execute = getattr(execute_tool, "__batch_execute__", None)
            assert batch_execute is not None
            batch_results = await batch_execute(
                [
                    ToolCallRequest(
                        tool_call_id="parallel-call-1",
                        tool_name="list_available_skills",
                        arguments={},
                    ),
                    ToolCallRequest(
                        tool_call_id="parallel-call-2",
                        tool_name="list_available_skills",
                        arguments={},
                    ),
                ]
            )
            assert [result.tool_name for result in batch_results] == [
                "list_available_skills",
                "list_available_skills",
            ]
            return f"parallel:{len(batch_results)}"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: ParallelReadonlyChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Readonly Parallel Batch"})
        session_id = api_data(session_response)["id"]

        with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
            chat_response = client.post(
                f"/api/sessions/{session_id}/chat",
                json={
                    "content": "run readonly batch",
                    "attachments": [],
                    "wait_for_completion": True,
                },
            )

            assert chat_response.status_code == 200
            chat_payload = api_data(chat_response)
            assert chat_payload["assistant_message"]["content"] == "parallel:2"

            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "session.updated" and event["payload"].get("status") == "done":
                    break

        event_types = [event["type"] for event in events]
        assert event_types.count("tool.call.started") == 2
        assert event_types.count("tool.call.finished") == 2
        assert "tool.call.failed" not in event_types

        transcript = chat_payload["assistant_message"]["assistant_transcript"]
        tool_call_ids = [
            segment["tool_call_id"]
            for segment in transcript
            if segment["kind"] == "tool_call" and segment["tool_name"] == "list_available_skills"
        ]
        tool_result_ids = [
            segment["tool_call_id"]
            for segment in transcript
            if segment["kind"] == "tool_result" and segment["tool_name"] == "list_available_skills"
        ]
        assert tool_call_ids[:2] == ["parallel-call-1", "parallel-call-2"]
        assert tool_result_ids[:2] == ["parallel-call-1", "parallel-call-2"]
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_failure_marks_session_error(client: TestClient) -> None:
    class FailingChatRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                execute_tool,
                callbacks,
            )
            raise ChatRuntimeError("LLM request timed out.")

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: FailingChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Failure Session"})
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "hello failure", "attachments": [], "wait_for_completion": True},
        )

        assert chat_response.status_code == 502
        assert chat_response.json()["detail"] == "LLM request timed out."

        detail_response = client.get(f"/api/sessions/{session_id}")
        assert detail_response.status_code == 200
        detail_payload = api_data(detail_response)
        assert detail_payload["status"] == "error"
        assert [message["role"] for message in detail_payload["messages"]] == ["user", "assistant"]
        assert detail_payload["messages"][1]["content"] == ""
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_failure_emits_generation_failed_and_trace_events(client: TestClient) -> None:
    class FailingChatRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                execute_tool,
                callbacks,
            )
            raise ChatRuntimeError("synthetic failure")

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: FailingChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Failure Event Session"})
        session_id = api_data(session_response)["id"]

        with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
            chat_response_box, worker = _post_chat_in_thread(
                client,
                session_id,
                {"content": "break", "attachments": [], "wait_for_completion": True},
            )

            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "session.updated" and event["payload"].get("status") == "error":
                    break

        worker.join(timeout=5)
        assert chat_response_box["value"] is not None
        assert chat_response_box["value"].status_code == 502

        event_types = [event["type"] for event in events]
        assert "generation.failed" in event_types
        assert "assistant.trace" in event_types
        graph_updates = [event for event in events if event["type"] == "graph.updated"]
        assert graph_updates
        assert any(event["payload"].get("graph_type") == "attack" for event in graph_updates)
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_failure_logs_runtime_error_to_backend_logger(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    class FailingChatRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                execute_tool,
                callbacks,
            )
            raise ChatRuntimeError("synthetic failure")

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: FailingChatRuntime()

    caplog.set_level(logging.ERROR, logger="aegissec.api")
    caplog.clear()

    try:
        session_response = client.post("/api/sessions", json={"title": "Failure Log Session"})
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "break", "attachments": [], "wait_for_completion": True},
        )

        assert chat_response.status_code == 502

        conversation_response = client.get(f"/api/sessions/{session_id}/conversation")
        assert conversation_response.status_code == 200
        generation_id = api_data(conversation_response)["generations"][0]["id"]

        matching_records = [
            record
            for record in caplog.records
            if record.name == "aegissec.api"
            and "Session worker generation failed during model/runtime execution"
            in record.getMessage()
        ]

        assert matching_records
        assert any(
            getattr(record, "session_id", None) == session_id
            and getattr(record, "generation_id", None) == generation_id
            and "synthetic failure" in record.getMessage()
            for record in matching_records
        )
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_unexpected_exception_closes_running_generation(client: TestClient) -> None:
    class UnexpectedFailureRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                execute_tool,
            )
            assert callbacks is not None
            assert callbacks.on_text_delta is not None
            await callbacks.on_text_delta("partial ")
            raise RuntimeError("unexpected runtime failure")

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: UnexpectedFailureRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Unexpected Failure"})
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "trigger failure", "attachments": [], "wait_for_completion": True},
        )
        assert chat_response.status_code == 502
        assert chat_response.json()["detail"] == "unexpected runtime failure"

        conversation_payload = None
        for _ in range(int(TEST_EVENTUAL_TIMEOUT_SECONDS / TEST_POLL_INTERVAL_SECONDS)):
            conversation_response = client.get(f"/api/sessions/{session_id}/conversation")
            assert conversation_response.status_code == 200
            conversation_payload = api_data(conversation_response)
            if (
                conversation_payload["session"]["status"] == "error"
                and conversation_payload["active_generation_id"] is None
                and conversation_payload["generations"]
                and conversation_payload["generations"][0]["status"] == "failed"
            ):
                break
            time.sleep(TEST_POLL_INTERVAL_SECONDS)

        assert conversation_payload is not None
        assert conversation_payload["session"]["status"] == "error"
        assert conversation_payload["active_generation_id"] is None
        assert conversation_payload["queued_generation_count"] == 0
        assert conversation_payload["generations"][0]["status"] == "failed"

        assistant_message = next(
            message
            for message in reversed(conversation_payload["messages"])
            if message["role"] == "assistant"
        )
        assert assistant_message["status"] == "failed"
        assert assistant_message["content"].startswith("partial")

        queue_payload = api_data(client.get(f"/api/sessions/{session_id}/queue"))
        assert queue_payload["active_generation"] is None
        assert queue_payload["active_generation_id"] is None
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_startup_recovery_requeues_abandoned_generations(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Recovery Session"})
    session_id = api_data(session_response)["id"]
    db_engine = app.state.database_engine

    with DBSession(db_engine) as db_session:
        repository = SessionRepository(db_session)
        session = repository.get_session(session_id)
        assert session is not None
        branch = repository.ensure_active_branch(session)
        user_message = repository.create_message(
            session=session,
            role=MessageRole.USER,
            content="recover me",
            attachments=[],
            branch_id=branch.id,
        )
        assistant_message = repository.create_message(
            session=session,
            role=MessageRole.ASSISTANT,
            content="partial",
            attachments=[],
            branch_id=branch.id,
            parent_message_id=user_message.id,
            status=MessageStatus.STREAMING,
        )
        generation = repository.create_generation(
            session_id=session.id,
            branch_id=branch.id,
            assistant_message_id=assistant_message.id,
            user_message_id=user_message.id,
        )
        repository.update_generation(
            generation,
            status=GenerationStatus.RUNNING,
            worker_id="worker-old",
            lease_claimed_at=datetime.now(UTC) - timedelta(minutes=10),
            lease_expires_at=datetime.now(UTC) - timedelta(minutes=5),
        )

    recovered_count = recover_abandoned_generations(db_engine)
    assert recovered_count == 1

    with DBSession(db_engine) as db_session:
        repository = SessionRepository(db_session)
        recovered_generation = repository.get_generation(generation.id)
        assert recovered_generation is not None
        assert recovered_generation.status == GenerationStatus.QUEUED
        assert recovered_generation.worker_id is None
        assert recovered_generation.lease_claimed_at is None
        assert recovered_generation.lease_expires_at is None


def test_session_runner_source_has_no_routes_chat_fallback_import() -> None:
    session_runner_source = Path(harness_session_runner.__file__).read_text(encoding="utf-8")
    assert 'importlib.import_module("app.api.routes_chat")' not in session_runner_source
    assert "app.api.routes_chat" not in session_runner_source


def test_startup_recovery_abandons_stale_continuation_and_requeues_generation(
    client: TestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    class ApprovalPauseRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                callbacks,
            )
            assert execute_tool is not None
            spawn_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="spawn-call-restart",
                    tool_name="spawn_subagent",
                    arguments={
                        "profile_name": "planner_agent",
                        "objective": "Plan the next attack path.",
                    },
                )
            )
            agent_id = cast(str, spawn_result.payload["agent"]["agent_id"])
            stop_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="stop-call-restart",
                    tool_name="stop_subagent",
                    arguments={"agent_id": agent_id, "reason": "Need operator approval."},
                )
            )
            return f"stopped:{stop_result.payload['agent_id']}"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: ApprovalPauseRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Restart Recovery Session"})
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "stop planner", "attachments": [], "wait_for_completion": True},
        )
        assert chat_response.status_code == 409
        stale_continuation_token = chat_response.json()["detail"]["continuation_token"]

        db_engine = app.state.database_engine
        with DBSession(db_engine) as db_session:
            repository = SessionRepository(db_session)
            active_generation = repository.get_active_generation(session_id)
            assert active_generation is not None
            generation_id = active_generation.id
            assistant_message_id = active_generation.assistant_message_id
            repository.update_generation(
                active_generation,
                status=GenerationStatus.RUNNING,
                worker_id="worker-restart",
                lease_claimed_at=datetime.now(UTC) - timedelta(minutes=10),
                lease_expires_at=datetime.now(UTC) - timedelta(minutes=5),
            )

        recovered_count = recover_abandoned_generations(db_engine)
        assert recovered_count == 1

        generation_manager = get_generation_manager()
        generation_manager._states.pop(session_id, None)

        with DBSession(db_engine) as db_session:
            repository = SessionRepository(db_session)
            recovered_generation = repository.get_generation(generation_id)
            recovered_assistant_message = repository.get_message(assistant_message_id)
            assert recovered_generation is not None
            assert recovered_assistant_message is not None
            assert recovered_generation.status == GenerationStatus.QUEUED
            assert "pending_continuation" not in recovered_generation.metadata_json
            assert "pending_continuation" not in recovered_assistant_message.metadata_json

        stale_resolve_response = client.post(
            f"/api/sessions/{session_id}/continuations/{stale_continuation_token}/resolve",
            json={"approve": True},
        )
        assert stale_resolve_response.status_code == 409
        assert stale_resolve_response.json()["detail"]["error"] == "already_aborted"

        session_runner_importlib = harness_session_runner.__dict__["importlib"]
        original_import_module = session_runner_importlib.import_module

        def guarded_import_module(name: str, package: str | None = None) -> object:
            if name == "app.api.routes_chat":
                raise AssertionError(
                    "session_runner should not import routes_chat during worker start"
                )
            return original_import_module(name, package)

        monkeypatch.setattr(session_runner_importlib, "import_module", guarded_import_module)

        resume_response = client.post(f"/api/sessions/{session_id}/resume")
        assert resume_response.status_code == 200

        new_continuation_token: str | None = None
        deadline = time.time() + TEST_EVENTUAL_TIMEOUT_SECONDS
        while time.time() < deadline:
            with DBSession(db_engine) as db_session:
                repository = SessionRepository(db_session)
                recovered_generation = repository.get_generation(generation_id)
                assert recovered_generation is not None
                pending_continuation = recovered_generation.metadata_json.get(
                    "pending_continuation"
                )
                if isinstance(pending_continuation, dict):
                    candidate = pending_continuation.get("continuation_token")
                    if isinstance(candidate, str) and candidate != stale_continuation_token:
                        new_continuation_token = candidate
                        break
            time.sleep(TEST_POLL_INTERVAL_SECONDS)
        else:
            pytest.fail("generation did not pause again with a new continuation token")

        resolve_response = client.post(
            f"/api/sessions/{session_id}/continuations/{new_continuation_token}/resolve",
            json={"approve": True},
        )
        assert resolve_response.status_code == 200

        deadline = time.time() + TEST_EVENTUAL_TIMEOUT_SECONDS
        while time.time() < deadline:
            conversation_response = client.get(f"/api/sessions/{session_id}/conversation")
            assert conversation_response.status_code == 200
            conversation_payload = api_data(conversation_response)
            assistant_message = next(
                (
                    message
                    for message in reversed(conversation_payload["messages"])
                    if message["role"] == "assistant"
                ),
                None,
            )
            if assistant_message and assistant_message["status"] == "completed":
                assert assistant_message["content"].startswith("stopped:")
                break
            time.sleep(TEST_POLL_INTERVAL_SECONDS)
        else:
            pytest.fail("assistant message did not complete after recovered continuation")
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_preserves_think_blocks_in_persisted_content_and_transcript_by_default(
    client: TestClient,
) -> None:
    class UnsafeReasoningChatRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                execute_tool,
            )
            assert callbacks is not None
            assert callbacks.on_summary is not None
            assert callbacks.on_text_delta is not None
            await callbacks.on_summary("<think>private</think>")
            await callbacks.on_text_delta("<thi")
            await callbacks.on_text_delta("nk>hidden</thi")
            await callbacks.on_text_delta("nk>")
            await callbacks.on_text_delta("<mini")
            await callbacks.on_text_delta(
                'max:tool_call><invoke name="agent-browser">{"task":"demo"}'
            )
            await callbacks.on_text_delta("</inv")
            await callbacks.on_text_delta("oke></minimax:tool_call>最终")
            return (
                '<minimax:tool_call><invoke name="agent-browser">{"task":"demo"}'
                "</invoke></minimax:tool_call><think>very secret</think>最终答复"
            )

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: UnsafeReasoningChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Safe Reasoning Session"})
        session_id = api_data(session_response)["id"]

        with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
            chat_response = client.post(
                f"/api/sessions/{session_id}/chat",
                json={"content": "请给出结果", "attachments": []},
            )
            assert chat_response.status_code == 200
            initial_chat_payload = api_data(chat_response)
            assert initial_chat_payload["generation"]["status"] == "queued"

            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "session.updated" and event["payload"].get("status") in {
                    "done",
                    "error",
                }:
                    break

        conversation_response = client.get(f"/api/sessions/{session_id}/conversation")
        assert conversation_response.status_code == 200
        conversation_payload = api_data(conversation_response)
        assistant_messages = [
            message
            for message in conversation_payload["messages"]
            if message["role"] == "assistant"
        ]
        assert assistant_messages
        chat_payload = {
            "assistant_message": assistant_messages[-1],
            "generation": conversation_payload["generations"][0],
        }

        summary_events = [event for event in events if event["type"] == "assistant.summary"]
        trace_events = [event for event in events if event["type"] == "assistant.trace"]
        delta_events = [event for event in events if event["type"] == "message.delta"]
        completed_events = [event for event in events if event["type"] == "message.completed"]
        update_events = [event for event in events if event["type"] == "message.updated"]

        assert summary_events
        assert summary_events[-1]["payload"]["summary"] == "<think>private</think>"
        assert trace_events
        assert all(isinstance(event["payload"].get("sequence"), int) for event in trace_events)
        assert all(isinstance(event["payload"].get("recorded_at"), str) for event in trace_events)
        assert delta_events
        combined_delta = "".join(event["payload"]["delta"] for event in delta_events)
        assert "<think>hidden</think>" in combined_delta
        assert "<think>very secret</think>最终答复" in combined_delta
        assert any("<think>" in event["payload"]["delta"] for event in delta_events)
        assert all("invoke" not in event["payload"]["delta"] for event in delta_events)
        assert all("tool_call" not in event["payload"]["delta"] for event in delta_events)
        assert completed_events
        assert completed_events[-1]["payload"]["content"] == "<think>very secret</think>最终答复"
        assert "invoke" not in completed_events[-1]["payload"]["content"]
        assert "tool_call" not in completed_events[-1]["payload"]["content"]
        assert update_events
        assert any(
            any(
                segment["kind"] == "reasoning"
                for segment in event["payload"]["assistant_transcript"]
            )
            for event in update_events
            if isinstance(event["payload"].get("assistant_transcript"), list)
        )
        assert chat_payload["generation"] is not None
        persisted_trace = chat_payload["generation"]["reasoning_trace"]
        assert [entry["sequence"] for entry in persisted_trace] == list(
            range(1, len(persisted_trace) + 1)
        )
        assert all(isinstance(entry.get("recorded_at"), str) for entry in persisted_trace)
        summary_trace_entries = [
            entry for entry in persisted_trace if entry.get("state") == "summary.updated"
        ]
        assert summary_trace_entries
        assert all(entry["event"] == "assistant.summary" for entry in summary_trace_entries)
        assert [entry["summary"] for entry in summary_trace_entries] == ["<think>private</think>"]
        steps = chat_payload["generation"]["steps"]
        assert [step["sequence"] for step in steps] == list(range(1, len(steps) + 1))

        def _step_string_values(value: object) -> list[str]:
            if isinstance(value, str):
                return [value]
            if isinstance(value, list):
                values: list[str] = []
                for item in value:
                    values.extend(_step_string_values(item))
                return values
            if isinstance(value, dict):
                values = []
                for item in value.values():
                    values.extend(_step_string_values(item))
                return values
            return []

        step_values = [text for step in steps for text in _step_string_values(step)]
        assert any("<think>private</think>" in value for value in step_values)
        assert all("invoke" not in value for value in step_values)
        assert all("tool_call" not in value for value in step_values)
        assert any(
            step["kind"] == "reasoning" and step["safe_summary"] == "<think>private</think>"
            for step in steps
        )
        assert any(
            step["kind"] == "output" and step["delta_text"] == "<think>very secret</think>最终答复"
            for step in steps
        )

        transcript = chat_payload["assistant_message"]["assistant_transcript"]
        assert [segment["sequence"] for segment in transcript] == list(
            range(1, len(transcript) + 1)
        )
        reasoning_segments = [segment for segment in transcript if segment["kind"] == "reasoning"]
        status_segments = [segment for segment in transcript if segment["kind"] == "status"]
        output_segments = [segment for segment in transcript if segment["kind"] == "output"]
        assert reasoning_segments
        status_texts = [segment["text"] for segment in status_segments]
        assert "开始生成回复" in status_texts
        assert "正在评估可预载技能" in status_texts
        assert "本轮生成已完成" in status_texts
        assert output_segments
        assert reasoning_segments[-1]["text"] == "<think>private</think>"
        assert output_segments[-1]["text"] == "<think>very secret</think>最终答复"

        detail_response = client.get(f"/api/sessions/{session_id}")
        assert detail_response.status_code == 200
        detail_payload = api_data(detail_response)
        assistant_messages = [
            message for message in detail_payload["messages"] if message["role"] == "assistant"
        ]
        assert assistant_messages
        assert assistant_messages[-1]["content"] == "<think>very secret</think>最终答复"
        assert "invoke" not in assistant_messages[-1]["content"]
        assert "tool_call" not in assistant_messages[-1]["content"]
        detail_transcript = assistant_messages[-1]["assistant_transcript"]
        assert [segment["kind"] for segment in detail_transcript] == [
            segment["kind"] for segment in transcript
        ]
        assert [segment["text"] for segment in detail_transcript] == [
            segment["text"] for segment in transcript
        ]

        with TestClient(app) as fresh_client:
            refreshed_detail_response = fresh_client.get(f"/api/sessions/{session_id}")
            assert refreshed_detail_response.status_code == 200
            refreshed_payload = api_data(refreshed_detail_response)
            refreshed_assistant_messages = [
                message
                for message in refreshed_payload["messages"]
                if message["role"] == "assistant"
            ]
            assert refreshed_assistant_messages
            assert refreshed_assistant_messages[-1]["content"] == (
                "<think>very secret</think>最终答复"
            )
            refreshed_transcript = refreshed_assistant_messages[-1]["assistant_transcript"]
            assert [segment["kind"] for segment in refreshed_transcript] == [
                segment["kind"] for segment in transcript
            ]
            assert [segment["text"] for segment in refreshed_transcript] == [
                segment["text"] for segment in transcript
            ]
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_preserves_think_blocks_in_assistant_history_for_follow_up_turns(
    client: TestClient,
) -> None:
    class HistoryAwareChatRuntime:
        def __init__(self) -> None:
            self.call_count = 0

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
            del attachments, available_skills, skill_context_prompt, execute_tool, callbacks
            self.call_count += 1
            if self.call_count == 1:
                assert content == "第一轮提示"
                return "<think>remember me</think>首轮答复"

            assert conversation_messages is not None
            assistant_messages = [
                message
                for message in conversation_messages
                if getattr(message, "role", None) == MessageRole.ASSISTANT
            ]
            assert assistant_messages
            assert getattr(assistant_messages[-1], "content", None) == (
                "<think>remember me</think>首轮答复"
            )
            return "第二轮答复"

    runtime = HistoryAwareChatRuntime()
    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: runtime

    try:
        session_response = client.post("/api/sessions", json={"title": "History Think Session"})
        session_id = api_data(session_response)["id"]

        first_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "第一轮提示", "attachments": [], "wait_for_completion": True},
        )
        assert first_response.status_code == 200
        assert api_data(first_response)["assistant_message"]["content"] == (
            "<think>remember me</think>首轮答复"
        )

        second_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "第二轮提示", "attachments": [], "wait_for_completion": True},
        )
        assert second_response.status_code == 200
        assert api_data(second_response)["assistant_message"]["content"] == "第二轮答复"
        assert runtime.call_count == 2
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_appends_reasoning_transcript_segments_for_multiple_summaries(
    client: TestClient,
) -> None:
    class MultiSummaryChatRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                execute_tool,
            )
            assert callbacks is not None
            assert callbacks.on_summary is not None
            assert callbacks.on_text_delta is not None

            await callbacks.on_summary("<think>first</think>初步分析")
            await callbacks.on_text_delta("中间输出")
            await callbacks.on_summary("<think>second</think>进一步分析")
            return "<think>final</think>最终答复"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: MultiSummaryChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Multi Summary Session"})
        session_id = api_data(session_response)["id"]

        with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
            chat_response = client.post(
                f"/api/sessions/{session_id}/chat",
                json={"content": "继续推理", "attachments": [], "wait_for_completion": True},
            )
            assert chat_response.status_code == 200
            chat_payload = api_data(chat_response)

            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "session.updated" and event["payload"].get("status") == "done":
                    break

        summary_events = [event for event in events if event["type"] == "assistant.summary"]
        assert [event["payload"]["summary"] for event in summary_events] == [
            "<think>first</think>初步分析",
            "<think>second</think>进一步分析",
        ]

        transcript = chat_payload["assistant_message"]["assistant_transcript"]
        reasoning_segments = [segment for segment in transcript if segment["kind"] == "reasoning"]
        output_segments = [segment for segment in transcript if segment["kind"] == "output"]

        assert [segment["text"] for segment in reasoning_segments] == [
            "<think>first</think>初步分析",
            "<think>second</think>进一步分析",
        ]
        assert output_segments[-1]["text"] == "<think>final</think>最终答复"

        persisted_trace = chat_payload["generation"]["reasoning_trace"]
        summary_trace_entries = [
            entry for entry in persisted_trace if entry.get("state") == "summary.updated"
        ]
        assert [entry["summary"] for entry in summary_trace_entries] == [
            "<think>first</think>初步分析",
            "<think>second</think>进一步分析",
        ]
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_preserves_interleaved_output_segments_around_tool_events(
    client: TestClient,
) -> None:
    class InterleavedToolChatRuntime:
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
            assert callbacks.on_text_delta is not None

            await callbacks.on_text_delta("前置分析")
            await execute_tool(
                ToolCallRequest(
                    tool_call_id="tool-call-1",
                    tool_name="execute_kali_command",
                    arguments={
                        "command": "printf 'auto tool' > reports/interleaved.txt",
                        "timeout_seconds": 10,
                        "artifact_paths": ["reports/interleaved.txt"],
                    },
                )
            )
            await callbacks.on_text_delta("后续结论")
            return "前置分析后续结论"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: InterleavedToolChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Interleaved Transcript"})
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "请按阶段处理", "attachments": [], "wait_for_completion": True},
        )

        assert chat_response.status_code == 200
        transcript = api_data(chat_response)["assistant_message"]["assistant_transcript"]
        ordered_segments = [
            segment
            for segment in transcript
            if segment["kind"] in {"output", "tool_call", "tool_result"}
        ]

        assert [segment["kind"] for segment in ordered_segments] == [
            "output",
            "tool_call",
            "tool_result",
            "output",
        ]
        assert [
            segment.get("text") for segment in ordered_segments if segment["kind"] == "output"
        ] == [
            "前置分析",
            "后续结论",
        ]
        assert ordered_segments[1]["tool_call_id"] == "tool-call-1"
        assert ordered_segments[2]["metadata"]["stdout"] == "runtime command completed"
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_can_list_loaded_skills_via_tool(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_skills(
        client,
        monkeypatch,
        tmp_path,
        {
            "adscan": """---
name: adscan
description: Active Directory 枚举 skill
compatibility: [opencode]
---
# adscan

Use when performing Active Directory pentest orchestration.
""",
            "docx": """---
name: docx
description: Document skill
---
# docx

Create and edit Word documents.
""",
        },
    )

    class ListSkillsChatRuntime:
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
            del content, attachments, conversation_messages, skill_context_prompt, callbacks
            assert available_skills is not None
            assert execute_tool is not None

            tool_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="skills-call-1",
                    tool_name="list_available_skills",
                    arguments={},
                )
            )
            names = [skill["directory_name"] for skill in tool_result.payload["skills"]]
            return "已加载 skills: " + ", ".join(names)

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: ListSkillsChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "List Skills Session"})
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "列出所有 skill", "attachments": [], "wait_for_completion": True},
        )

        assert chat_response.status_code == 200
        assert (
            api_data(chat_response)["assistant_message"]["content"] == "已加载 skills: adscan, docx"
        )
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_can_read_skill_content_via_tool(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_skills(
        client,
        monkeypatch,
        tmp_path,
        {
            "adscan": """---
name: adscan
description: Active Directory 枚举 skill
compatibility: [opencode]
---
# adscan

Use when performing Active Directory pentest orchestration without using ADscan itself.
""",
        },
    )

    class ReadSkillChatRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                callbacks,
            )
            assert execute_tool is not None

            tool_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="skills-call-2",
                    tool_name="read_skill_content",
                    arguments={"skill_name_or_id": "adscan"},
                )
            )
            skill = tool_result.payload["skill"]
            return f"{skill['directory_name']}: {skill['content'].splitlines()[0]}"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: ReadSkillChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Read Skill Session"})
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "查看 adscan skill", "attachments": [], "wait_for_completion": True},
        )

        assert chat_response.status_code == 200
        assert api_data(chat_response)["assistant_message"]["content"] == "adscan: ---"
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_can_execute_skill_via_tool(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_skills(
        client,
        monkeypatch,
        tmp_path,
        {
            "adscan": """---
name: adscan
description: Active Directory 枚举 skill
compatibility: [opencode]
---
# adscan

Use when performing Active Directory pentest orchestration without using ADscan itself.
""",
        },
    )

    class ExecuteSkillChatRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                callbacks,
            )
            assert execute_tool is not None

            tool_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="skills-call-execute-1",
                    tool_name="execute_skill",
                    arguments={"skill_name_or_id": "adscan"},
                )
            )
            execution = tool_result.payload["execution"]
            skill = tool_result.payload["skill"]
            return f"{skill['directory_name']}: {execution['status']}"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: ExecuteSkillChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Execute Skill Session"})
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "执行 adscan skill", "attachments": [], "wait_for_completion": True},
        )

        assert chat_response.status_code == 200
        chat_payload = api_data(chat_response)
        assert chat_payload["assistant_message"]["content"] == "adscan: prepared"
        transcript = chat_payload["assistant_message"]["assistant_transcript"]
        tool_call_segment = next(
            segment
            for segment in transcript
            if segment["kind"] == "tool_call" and segment["tool_call_id"] == "skills-call-execute-1"
        )
        tool_result_segment = next(
            segment
            for segment in transcript
            if segment["kind"] == "tool_result"
            and segment["tool_call_id"] == "skills-call-execute-1"
        )
        assert tool_call_segment["tool_name"] == "execute_skill"
        assert tool_result_segment["metadata"]["result"]["execution"]["status"] == "prepared"
        assert tool_result_segment["metadata"]["result"]["skill"]["directory_name"] == "adscan"
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_can_call_mcp_tool_via_dynamic_alias(client: TestClient) -> None:
    class FakeMCPService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict[str, object]]] = []

        def list_servers(self) -> list[MCPServerRead]:
            return [
                MCPServerRead(
                    id="server-1",
                    name="Burp Suite",
                    source=CompatibilitySource.LOCAL,
                    scope=CompatibilityScope.PROJECT,
                    transport=MCPTransport.STDIO,
                    enabled=True,
                    timeout_ms=30_000,
                    status=MCPServerStatus.CONNECTED,
                    config_path="mcp.json",
                    imported_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
                    capabilities=[
                        MCPCapabilityRead(
                            kind=MCPCapabilityKind.TOOL,
                            name="scan-target",
                            title="Scan Target",
                            description="Run a focused MCP scan.",
                            input_schema={
                                "type": "object",
                                "properties": {"target": {"type": "string"}},
                                "required": ["target"],
                                "additionalProperties": False,
                            },
                        )
                    ],
                )
            ]

        async def call_tool(
            self,
            server_id: str,
            tool_name: str,
            arguments: dict[str, object],
        ) -> dict[str, object]:
            self.calls.append((server_id, tool_name, dict(arguments)))
            return {"content": [{"type": "text", "text": "scan ok"}]}

    class MCPAliasChatRuntime:
        async def generate_reply(
            self,
            content: str,
            attachments: list[object],
            conversation_messages: list[object] | None = None,
            available_skills: list[object] | None = None,
            mcp_tools: list[object] | None = None,
            skill_context_prompt: str | None = None,
            execute_tool: ToolExecutor | None = None,
            callbacks: GenerationCallbacks | None = None,
        ) -> str:
            del content, attachments, conversation_messages, available_skills, callbacks
            assert execute_tool is not None
            assert mcp_tools == [
                {
                    "tool_alias": "mcp__burp_suite__scan_target",
                    "server_id": "server-1",
                    "server_name": "Burp Suite",
                    "source": "local",
                    "scope": "project",
                    "transport": "stdio",
                    "tool_name": "scan-target",
                    "tool_title": "Scan Target",
                    "tool_description": "Run a focused MCP scan.",
                    "input_schema": {
                        "type": "object",
                        "properties": {"target": {"type": "string"}},
                        "required": ["target"],
                        "additionalProperties": False,
                    },
                }
            ]
            assert skill_context_prompt is not None
            assert "mcp__burp_suite__scan_target: Burp Suite / scan-target" in skill_context_prompt

            tool_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="mcp-call-1",
                    tool_name="mcp__burp_suite__scan_target",
                    arguments={"target": "https://example.test"},
                    mcp_server_id="server-1",
                    mcp_tool_name="scan-target",
                )
            )
            return str(tool_result.payload["result"]["content"][0]["text"])

    fake_mcp_service = FakeMCPService()
    original_runtime_override = app.dependency_overrides[get_chat_runtime]
    original_mcp_override = app.dependency_overrides.get(get_mcp_service)
    app.dependency_overrides[get_chat_runtime] = lambda: MCPAliasChatRuntime()
    app.dependency_overrides[get_mcp_service] = lambda: fake_mcp_service

    try:
        session_response = client.post("/api/sessions", json={"title": "MCP Alias Session"})
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "run mcp scan", "attachments": [], "wait_for_completion": True},
        )

        assert chat_response.status_code == 200
        assert api_data(chat_response)["assistant_message"]["content"] == "scan ok"
        assert fake_mcp_service.calls == [
            ("server-1", "scan-target", {"target": "https://example.test"})
        ]
    finally:
        app.dependency_overrides[get_chat_runtime] = original_runtime_override
        if original_mcp_override is None:
            app.dependency_overrides.pop(get_mcp_service, None)
        else:
            app.dependency_overrides[get_mcp_service] = original_mcp_override


def test_chat_autoroutes_docx_skill_context_on_exact_skill_mention(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_skills(
        client,
        monkeypatch,
        tmp_path,
        {
            "docx": """---
name: docx
description: Word document editing helper
---
# docx

Create and edit Word documents.
""",
            "ctf-web": """---
name: ctf-web
description: Web CTF exploitation playbook
---
# ctf-web

Focus on web-CTF workflows including XSS, SQLi, file inclusion, and login bypass.
""",
        },
    )

    captured_skill_context_prompt: dict[str, str | None] = {"value": None}

    class GenericAutoRouteSkillRuntime:
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
            del content, attachments, conversation_messages, execute_tool, callbacks
            assert available_skills is not None
            assert any(
                getattr(skill, "directory_name", None) == "docx"
                or getattr(skill, "name", None) == "docx"
                for skill in available_skills
            )
            captured_skill_context_prompt["value"] = skill_context_prompt
            return "已收到 docx 自动技能上下文"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: GenericAutoRouteSkillRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Docx Autoroute"})
        session_id = api_data(session_response)["id"]

        with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
            chat_response = client.post(
                f"/api/sessions/{session_id}/chat",
                json={
                    "content": "帮我整理这份 docx 文档并补齐格式",
                    "attachments": [],
                    "wait_for_completion": True,
                },
            )
            assert chat_response.status_code == 200
            chat_payload = api_data(chat_response)

            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "session.updated" and event["payload"].get("status") == "done":
                    break

        assert any(event["type"] == "tool.call.started" for event in events)
        assert any(event["type"] == "tool.call.finished" for event in events)

        transcript = chat_payload["assistant_message"]["assistant_transcript"]
        status_segments = [segment for segment in transcript if segment["kind"] == "status"]
        relevant_segments = [
            segment
            for segment in transcript
            if segment["kind"] in {"tool_call", "tool_result", "output"}
        ]
        assert status_segments
        assert [segment["kind"] for segment in relevant_segments[:3]] == [
            "tool_call",
            "tool_result",
            "output",
        ]
        assert relevant_segments[0]["tool_name"] == "execute_skill"
        selected_skill = relevant_segments[1]["metadata"]["result"]["skill"]["directory_name"]
        assert isinstance(selected_skill, str) and selected_skill
        assert relevant_segments[1]["metadata"]["result"]["execution"]["status"] == "prepared"
        autorouted_skill = chat_payload["generation"]["metadata"]["prompt_provenance"][
            "autorouted_skill"
        ]
        assert autorouted_skill["state"] == "skill.autoroute.finished"
        assert autorouted_skill["skill"] == selected_skill
        assert autorouted_skill["context_injected"] is True
        assert autorouted_skill["confidence"] >= 70
        assert cast(list[dict[str, object]], autorouted_skill["candidates"])
        prompt_text = captured_skill_context_prompt["value"]
        assert prompt_text is not None
        assert selected_skill in prompt_text
        assert (
            f"Prepared primary skill: {selected_skill}" in prompt_text
            or "Primary skill:" in prompt_text
            or f"# {selected_skill}" in prompt_text
        )
        assert chat_payload["assistant_message"]["content"] == "已收到 docx 自动技能上下文"
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_governance_denies_write_command_before_runtime_execution(client: TestClient) -> None:
    class DeniedToolChatRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                callbacks,
            )
            assert execute_tool is not None
            await execute_tool(
                ToolCallRequest(
                    tool_call_id="tool-call-1",
                    tool_name="execute_kali_command",
                    arguments={
                        "command": "touch reports/blocked.txt",
                        "timeout_seconds": 10,
                        "artifact_paths": ["reports/blocked.txt"],
                    },
                )
            )
            return "unreachable"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: DeniedToolChatRuntime()

    try:
        session_response = client.post(
            "/api/sessions",
            json={
                "title": "Governance Denial",
                "runtime_policy_json": {
                    "allow_network": True,
                    "allow_write": False,
                    "max_execution_seconds": 300,
                    "max_command_length": 4000,
                },
            },
        )
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "尝试写文件", "attachments": [], "wait_for_completion": True},
        )

        assert chat_response.status_code == 502
        assert "Runtime policy blocks write-capable commands." in chat_response.text
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_can_spawn_swarm_subagent_via_tool(client: TestClient) -> None:
    class SwarmToolChatRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                callbacks,
            )
            assert execute_tool is not None
            tool_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="swarm-call-1",
                    tool_name="spawn_subagent",
                    arguments={
                        "profile_name": "planner_agent",
                        "objective": "Plan the next attack path.",
                    },
                )
            )
            agent = tool_result.payload["agent"]
            task = tool_result.payload["task"]
            notifications = tool_result.payload["notifications"]
            assert any(item["status"] == "planned" for item in notifications)
            assert any(item["status"] == "started" for item in notifications)
            return f"{agent['profile_name']}:{task['status']}"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: SwarmToolChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Swarm Session"})
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "spawn planner", "attachments": [], "wait_for_completion": True},
        )

        assert chat_response.status_code == 200
        chat_payload = api_data(chat_response)
        assert chat_payload["assistant_message"]["content"] == "planner_agent:in_progress"
        transcript = chat_payload["assistant_message"]["assistant_transcript"]
        tool_result_segment = next(
            segment
            for segment in transcript
            if segment["kind"] == "tool_result" and segment["tool_call_id"] == "swarm-call-1"
        )
        assert tool_result_segment["metadata"]["result"]["agent"]["profile_name"] == "planner_agent"
        assert tool_result_segment["metadata"]["result"]["task"]["status"] == "in_progress"
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_pauses_and_resumes_approval_required_tool(client: TestClient) -> None:
    class ApprovalPauseRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                callbacks,
            )
            assert execute_tool is not None
            spawn_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="spawn-call-1",
                    tool_name="spawn_subagent",
                    arguments={
                        "profile_name": "planner_agent",
                        "objective": "Plan the next attack path.",
                    },
                )
            )
            agent_id = cast(str, spawn_result.payload["agent"]["agent_id"])
            stop_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="stop-call-1",
                    tool_name="stop_subagent",
                    arguments={"agent_id": agent_id, "reason": "Need operator approval."},
                )
            )
            return f"stopped:{stop_result.payload['agent_id']}"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: ApprovalPauseRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Approval Pause Session"})
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "stop planner", "attachments": [], "wait_for_completion": True},
        )

        assert chat_response.status_code == 409
        detail = chat_response.json()["detail"]
        assert detail["action"] == "require_approval"
        continuation_token = detail["continuation_token"]

        resolve_response = client.post(
            f"/api/sessions/{session_id}/continuations/{continuation_token}/resolve",
            json={"approve": True},
        )
        assert resolve_response.status_code == 200
        assert api_data(resolve_response)["session"]["status"] == "running"

        deadline = time.time() + TEST_EVENTUAL_TIMEOUT_SECONDS
        while time.time() < deadline:
            conversation_response = client.get(f"/api/sessions/{session_id}/conversation")
            assert conversation_response.status_code == 200
            conversation_payload = api_data(conversation_response)
            assistant_message = next(
                (
                    message
                    for message in reversed(conversation_payload["messages"])
                    if message["role"] == "assistant"
                ),
                None,
            )
            if assistant_message and assistant_message["status"] == "completed":
                assert assistant_message["content"].startswith("stopped:")
                break
            time.sleep(TEST_POLL_INTERVAL_SECONDS)
        else:
            pytest.fail("assistant message did not complete after continuation resolution")
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_pauses_and_rejects_approval_required_tool(client: TestClient) -> None:
    class ApprovalPauseRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                callbacks,
            )
            assert execute_tool is not None
            spawn_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="spawn-call-reject",
                    tool_name="spawn_subagent",
                    arguments={
                        "profile_name": "planner_agent",
                        "objective": "Plan the next attack path.",
                    },
                )
            )
            agent_id = cast(str, spawn_result.payload["agent"]["agent_id"])
            await execute_tool(
                ToolCallRequest(
                    tool_call_id="stop-call-reject",
                    tool_name="stop_subagent",
                    arguments={"agent_id": agent_id, "reason": "Need operator approval."},
                )
            )
            return "unreachable"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: ApprovalPauseRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Rejected Approval Session"})
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "stop planner", "attachments": [], "wait_for_completion": True},
        )

        assert chat_response.status_code == 409
        continuation_token = chat_response.json()["detail"]["continuation_token"]

        resolve_response = client.post(
            f"/api/sessions/{session_id}/continuations/{continuation_token}/resolve",
            json={"approved": False, "user_input": "Denied by operator."},
        )
        assert resolve_response.status_code == 200
        resolution = api_data(resolve_response)["resolution"]
        assert resolution["approved"] is False
        assert resolution["outcome"] == "rejected"

        deadline = time.time() + TEST_EVENTUAL_TIMEOUT_SECONDS
        while time.time() < deadline:
            conversation_response = client.get(f"/api/sessions/{session_id}/conversation")
            assert conversation_response.status_code == 200
            conversation_payload = api_data(conversation_response)
            assistant_message = next(
                (
                    message
                    for message in reversed(conversation_payload["messages"])
                    if message["role"] == "assistant"
                ),
                None,
            )
            if assistant_message and assistant_message["status"] == "failed":
                break
            time.sleep(TEST_POLL_INTERVAL_SECONDS)
        else:
            pytest.fail("assistant message did not fail after approval rejection")
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_cannot_resolve_already_resolved_continuation(client: TestClient) -> None:
    class ApprovalPauseRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                callbacks,
            )
            assert execute_tool is not None
            spawn_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="spawn-call-resolved",
                    tool_name="spawn_subagent",
                    arguments={
                        "profile_name": "planner_agent",
                        "objective": "Plan the next attack path.",
                    },
                )
            )
            agent_id = cast(str, spawn_result.payload["agent"]["agent_id"])
            stop_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="stop-call-resolved",
                    tool_name="stop_subagent",
                    arguments={"agent_id": agent_id, "reason": "Need operator approval."},
                )
            )
            return f"stopped:{stop_result.payload['agent_id']}"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: ApprovalPauseRuntime()

    try:
        session_response = client.post(
            "/api/sessions", json={"title": "Already Resolved Continuation Session"}
        )
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "stop planner", "attachments": [], "wait_for_completion": True},
        )
        assert chat_response.status_code == 409
        continuation_token = chat_response.json()["detail"]["continuation_token"]

        resolve_response = client.post(
            f"/api/sessions/{session_id}/continuations/{continuation_token}/resolve",
            json={"approve": True},
        )
        assert resolve_response.status_code == 200

        second_resolve_response = client.post(
            f"/api/sessions/{session_id}/continuations/{continuation_token}/resolve",
            json={"approve": True},
        )
        assert second_resolve_response.status_code == 409
        detail = second_resolve_response.json()["detail"]
        assert detail["error"] == "already_resolved"
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_pauses_and_resumes_scope_confirmation_tool(
    client: TestClient, monkeypatch: MonkeyPatch
) -> None:
    checker_module = importlib.import_module("app.harness.governance.checker")
    original_evaluate = checker_module.DefaultHarnessToolDecisionChecker.evaluate

    def patched_evaluate(self: object, request: object) -> object:
        tool_request = getattr(request, "tool_request")
        if getattr(tool_request, "tool_name", None) == "list_available_skills":
            return checker_module.HarnessToolDecision(
                action="require_scope_confirmation",
                reason="Scope confirmation required.",
                metadata={"scope_miss": True},
            )
        return original_evaluate(self, request)

    monkeypatch.setattr(
        checker_module.DefaultHarnessToolDecisionChecker,
        "evaluate",
        patched_evaluate,
    )

    class ScopeConfirmationRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                callbacks,
            )
            assert execute_tool is not None
            listed_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="scope-call-1",
                    tool_name="list_available_skills",
                    arguments={},
                )
            )
            return f"listed:{len(cast(list[object], listed_result.payload['skills']))}"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: ScopeConfirmationRuntime()

    try:
        session_response = client.post(
            "/api/sessions", json={"title": "Scope Confirmation Session"}
        )
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "list skills", "attachments": [], "wait_for_completion": True},
        )

        assert chat_response.status_code == 409
        detail = chat_response.json()["detail"]
        assert detail["action"] == "require_scope_confirmation"
        continuation_token = detail["continuation_token"]

        resolve_response = client.post(
            f"/api/sessions/{session_id}/continuations/{continuation_token}/resolve",
            json={"approve": True, "resolution_payload": {"scope_confirmed": True}},
        )
        assert resolve_response.status_code == 200
        assert api_data(resolve_response)["session"]["status"] == "running"

        deadline = time.time() + TEST_EVENTUAL_TIMEOUT_SECONDS
        while time.time() < deadline:
            conversation_response = client.get(f"/api/sessions/{session_id}/conversation")
            assert conversation_response.status_code == 200
            conversation_payload = api_data(conversation_response)
            assistant_message = next(
                (
                    message
                    for message in reversed(conversation_payload["messages"])
                    if message["role"] == "assistant"
                ),
                None,
            )
            if assistant_message and assistant_message["status"] == "completed":
                assert assistant_message["content"].startswith("listed:")
                break
            time.sleep(TEST_POLL_INTERVAL_SECONDS)
        else:
            pytest.fail("assistant message did not complete after scope confirmation")
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_active_generation_inject_resolves_interaction_continuation(
    client: TestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    checker_module = importlib.import_module("app.harness.governance.checker")
    original_evaluate = checker_module.DefaultHarnessToolDecisionChecker.evaluate

    def patched_evaluate(self: object, request: object) -> object:
        tool_request = getattr(request, "tool_request")
        if getattr(tool_request, "tool_name", None) == "list_available_skills":
            return checker_module.HarnessToolDecision(
                action="require_scope_confirmation",
                reason="Scope confirmation required.",
                metadata={"scope_miss": True},
            )
        return original_evaluate(self, request)

    monkeypatch.setattr(
        checker_module.DefaultHarnessToolDecisionChecker,
        "evaluate",
        patched_evaluate,
    )

    class ScopeConfirmationRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                callbacks,
            )
            assert execute_tool is not None
            listed_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="scope-call-inject",
                    tool_name="list_available_skills",
                    arguments={},
                )
            )
            return f"listed:{len(cast(list[object], listed_result.payload['skills']))}"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: ScopeConfirmationRuntime()

    try:
        session_response = client.post(
            "/api/sessions", json={"title": "Interaction Inject Session"}
        )
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "list skills", "attachments": [], "wait_for_completion": True},
        )

        assert chat_response.status_code == 409
        assert chat_response.json()["detail"]["action"] == "require_scope_confirmation"

        inject_response = client.post(
            f"/api/sessions/{session_id}/generations/active/inject",
            json={"content": "范围已确认，请继续执行。"},
        )
        assert inject_response.status_code == 200
        inject_payload = api_data(inject_response)
        assert inject_payload["delivery"] == "paused_continuation"

        deadline = time.time() + TEST_EVENTUAL_TIMEOUT_SECONDS
        while time.time() < deadline:
            conversation_response = client.get(f"/api/sessions/{session_id}/conversation")
            assert conversation_response.status_code == 200
            conversation_payload = api_data(conversation_response)
            assistant_message = next(
                (
                    message
                    for message in reversed(conversation_payload["messages"])
                    if message["role"] == "assistant"
                ),
                None,
            )
            if assistant_message and assistant_message["status"] == "completed":
                assert assistant_message["content"].startswith("listed:")
                break
            time.sleep(TEST_POLL_INTERVAL_SECONDS)
        else:
            pytest.fail("assistant message did not complete after interaction injection")
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_active_generation_inject_rejects_approval_continuation(client: TestClient) -> None:
    class ApprovalPauseRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                callbacks,
            )
            assert execute_tool is not None
            spawn_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="spawn-call-approval-inject",
                    tool_name="spawn_subagent",
                    arguments={
                        "profile_name": "planner_agent",
                        "objective": "Plan the next attack path.",
                    },
                )
            )
            agent_id = cast(str, spawn_result.payload["agent"]["agent_id"])
            await execute_tool(
                ToolCallRequest(
                    tool_call_id="stop-call-approval-inject",
                    tool_name="stop_subagent",
                    arguments={"agent_id": agent_id, "reason": "Need operator approval."},
                )
            )
            return "unreachable"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: ApprovalPauseRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Approval Inject Session"})
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "stop planner", "attachments": [], "wait_for_completion": True},
        )
        assert chat_response.status_code == 409

        inject_response = client.post(
            f"/api/sessions/{session_id}/generations/active/inject",
            json={"content": "直接继续，不需要审批。"},
        )
        assert inject_response.status_code == 409
        detail = inject_response.json()["detail"]
        assert detail["error"] == "approval_required"
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_cannot_resolve_cancelled_continuation(client: TestClient) -> None:
    class ApprovalPauseRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                skill_context_prompt,
                callbacks,
            )
            assert execute_tool is not None
            spawn_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="spawn-call-cancel",
                    tool_name="spawn_subagent",
                    arguments={
                        "profile_name": "planner_agent",
                        "objective": "Plan the next attack path.",
                    },
                )
            )
            agent_id = cast(str, spawn_result.payload["agent"]["agent_id"])
            await execute_tool(
                ToolCallRequest(
                    tool_call_id="stop-call-cancel",
                    tool_name="stop_subagent",
                    arguments={"agent_id": agent_id, "reason": "Need operator approval."},
                )
            )
            return "unreachable"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: ApprovalPauseRuntime()

    try:
        session_response = client.post(
            "/api/sessions", json={"title": "Cancelled Continuation Session"}
        )
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "stop planner", "attachments": [], "wait_for_completion": True},
        )
        assert chat_response.status_code == 409
        continuation_token = chat_response.json()["detail"]["continuation_token"]

        cancel_response = client.post(f"/api/sessions/{session_id}/cancel")
        assert cancel_response.status_code == 200

        resolve_response = client.post(
            f"/api/sessions/{session_id}/continuations/{continuation_token}/resolve",
            json={"approve": True},
        )
        assert resolve_response.status_code == 409
        detail = resolve_response.json()["detail"]
        assert detail["error"] == "already_aborted"
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_invalid_continuation_token_returns_not_found(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Invalid Continuation Session"})
    session_id = api_data(session_response)["id"]

    resolve_response = client.post(
        f"/api/sessions/{session_id}/continuations/does-not-exist/resolve",
        json={"approve": True},
    )

    assert resolve_response.status_code == 404
    assert resolve_response.json()["detail"] == "Continuation not found"


def test_chat_autoroutes_ctf_web_skill_context_from_contextual_match(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_skills(
        client,
        monkeypatch,
        tmp_path,
        {
            "solve-challenge": """---
name: solve-challenge
description: CTF dispatcher
family: ctf
task_mode: dispatcher
tags: [ctf, dispatcher, challenge]
---
# solve-challenge

Coordinate challenge-solving workflows.
""",
            "ctf-web": """---
name: ctf-web
description: Web CTF exploitation playbook
family: ctf
domain: web
task_mode: specialized
tags: [ctf-web, web]
---
# ctf-web

Focus on web-CTF workflows.
""",
            "docx": """---
name: docx
description: Word document helper
---
# docx

Create and edit Word documents.
""",
        },
    )

    captured_skill_context_prompt: dict[str, str | None] = {"value": None}

    class ContextualCtfWebAutoRouteRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                execute_tool,
                callbacks,
            )
            captured_skill_context_prompt["value"] = skill_context_prompt
            return "已收到 solve-challenge + ctf-web 自动技能上下文"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: ContextualCtfWebAutoRouteRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "CTF Web Autoroute"})
        session_id = api_data(session_response)["id"]

        with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
            chat_response = client.post(
                f"/api/sessions/{session_id}/chat",
                json={
                    "content": "这个 web ctf 题的 node5.buuoj.cn 登录绕过、xss 和 sqli 怎么看？",
                    "attachments": [],
                    "wait_for_completion": True,
                },
            )

            assert chat_response.status_code == 200
            chat_payload = api_data(chat_response)

            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "session.updated" and event["payload"].get("status") == "done":
                    break

        transcript = chat_payload["assistant_message"]["assistant_transcript"]
        status_segments = [segment for segment in transcript if segment["kind"] == "status"]
        relevant_segments = [
            segment
            for segment in transcript
            if segment["kind"] in {"tool_call", "tool_result", "output"}
        ]
        prompt_text = captured_skill_context_prompt["value"]
        autorouted_skill = chat_payload["generation"]["metadata"]["prompt_provenance"][
            "autorouted_skill"
        ]
        assert autorouted_skill["state"] in {"skill.autoroute.finished", "skill.autoroute.skipped"}
        if autorouted_skill["state"] == "skill.autoroute.finished":
            assert any(segment["text"] == "自动选择 solve-challenge" for segment in status_segments)
            assert relevant_segments[0]["tool_name"] == "execute_skill"
            assert (
                relevant_segments[1]["metadata"]["result"]["skill"]["directory_name"]
                == "solve-challenge"
            )
            assert relevant_segments[1]["metadata"]["result"]["execution"]["status"] == "prepared"
            assert prompt_text is not None
            assert (
                "Prepared primary skill: solve-challenge" in prompt_text
                or "Primary skill:" in prompt_text
            )
            assert (
                "## Prepared skill context: primary=solve-challenge" in prompt_text
                or "solve-challenge | prepared_for_context=True | prepared_for_execution=True"
                in prompt_text
            )
            assert "# solve-challenge" in prompt_text
            assert "ctf-web: context=True execution=False" in prompt_text or (
                "ctf-web | prepared_for_context=True | prepared_for_execution=False" in prompt_text
            )
            assert autorouted_skill["skill"] == "solve-challenge"
            assert autorouted_skill["context_injected"] is True
            assert autorouted_skill["confidence"] >= 70
            assert any(
                candidate["skill"] == "solve-challenge"
                for candidate in cast(list[dict[str, object]], autorouted_skill["candidates"])
            )
        assert (
            chat_payload["assistant_message"]["content"]
            == "已收到 solve-challenge + ctf-web 自动技能上下文"
        )
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_skips_autoroute_when_skill_match_is_ambiguous(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_skills(
        client,
        monkeypatch,
        tmp_path,
        {
            "docx-helper": """---
name: docx helper
description: Docx helper for editing templates
---
# docx-helper

Use for docx helper workflows.
""",
            "helper-docx": """---
name: helper docx
description: Docx helper for editing templates
---
# helper-docx

Use for docx helper workflows.
""",
        },
    )

    class AmbiguousAutoRouteRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                execute_tool,
                callbacks,
            )
            assert skill_context_prompt is not None
            assert "Prepared primary skill:" not in skill_context_prompt
            assert "Primary skill:" not in skill_context_prompt
            return "保持手动选择"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: AmbiguousAutoRouteRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Ambiguous Autoroute"})
        session_id = api_data(session_response)["id"]

        with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
            chat_response = client.post(
                f"/api/sessions/{session_id}/chat",
                json={
                    "content": "我需要 helper 和 docx 来整理模板",
                    "attachments": [],
                    "wait_for_completion": True,
                },
            )
            assert chat_response.status_code == 200
            chat_payload = api_data(chat_response)

            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "session.updated" and event["payload"].get("status") == "done":
                    break

        assert not any(event["type"] == "tool.call.started" for event in events)
        status_segments = [
            segment
            for segment in chat_payload["assistant_message"]["assistant_transcript"]
            if segment["kind"] == "status"
        ]
        assert any(
            "存在多个高置信技能候选" in (segment["text"] or "") for segment in status_segments
        )
        assert chat_payload["assistant_message"]["content"] == "保持手动选择"
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_reports_autoroute_preload_failure_and_continues_generation(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    _seed_skills(
        client,
        monkeypatch,
        tmp_path,
        {
            "docx": """---
name: docx
description: Word document editing helper
---
# docx

Create and edit Word documents.
""",
        },
    )

    monkeypatch.setattr(
        "app.compat.skills.service.SkillService.execute_skill_by_name_or_directory_name",
        lambda self, name_or_slug, **kwargs: (_ for _ in ()).throw(
            SkillContentReadError(f"无法读取 {name_or_slug} 内容")
        ),
    )

    class FailedAutoRouteRuntime:
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
            del (
                content,
                attachments,
                conversation_messages,
                available_skills,
                execute_tool,
                callbacks,
            )
            if skill_context_prompt is not None:
                assert "Prepared primary skill: docx" not in skill_context_prompt
                assert "Primary skill:" not in skill_context_prompt
            return "继续普通流程"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: FailedAutoRouteRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Autoroute Failure"})
        session_id = api_data(session_response)["id"]
        wait_timeout_seconds = max(TEST_EVENTUAL_TIMEOUT_SECONDS * 5, 5.0)
        poll_interval_seconds = max(TEST_POLL_INTERVAL_SECONDS, 0.05)

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={
                "content": "请处理这个 docx 文件",
                "attachments": [],
            },
        )
        assert chat_response.status_code == 200
        chat_payload = api_data(chat_response)
        assert chat_payload["generation"]["status"] == "queued"

        conversation_payload: dict[str, object] | None = None
        assistant_message: dict[str, object] | None = None
        generation_status: str | None = None
        deadline = time.time() + wait_timeout_seconds
        while time.time() < deadline:
            conversation_response = client.get(f"/api/sessions/{session_id}/conversation")
            assert conversation_response.status_code == 200
            conversation_payload = api_data(conversation_response)
            assert conversation_payload is not None

            generations = cast(list[dict[str, object]], conversation_payload["generations"])
            if generations:
                generation_status = cast(str, generations[0]["status"])

            assistant_messages = [
                message
                for message in cast(list[dict[str, object]], conversation_payload["messages"])
                if message["role"] == "assistant"
            ]
            if assistant_messages:
                assistant_message = assistant_messages[-1]

            if generation_status in {"completed", "failed", "cancelled"} and assistant_message:
                break

            time.sleep(poll_interval_seconds)

        assert conversation_payload is not None
        assert generation_status in {"running", "completed"}, conversation_payload
        assert assistant_message is not None, conversation_payload

        if generation_status == "completed":
            transcript = cast(list[dict[str, object]], assistant_message["assistant_transcript"])
            autoroute_feedback_segments = [
                segment
                for segment in transcript
                if cast(str, segment.get("kind")) in {"status", "error"}
            ]
            assert autoroute_feedback_segments
            assert any(cast(str, segment.get("kind")) == "error" for segment in transcript)
            assert assistant_message["content"] == "继续普通流程"
        else:
            assert cast(str, assistant_message.get("status")) in {
                "pending",
                "queued",
                "streaming",
                "completed",
            }
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def _seed_skills(
    client: TestClient,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
    skill_bodies: dict[str, str],
) -> list[dict[str, object]]:
    local_root = tmp_path / "project" / "skills"
    for directory_name, content in skill_bodies.items():
        _write_skill(local_root / directory_name / "SKILL.md", content)

    monkeypatch.setattr(
        "app.compat.skills.service.resolve_skill_scan_roots",
        lambda _settings: [
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=str(local_root),
            ),
        ],
    )

    del client
    engine = getattr(app.state, "database_engine")
    settings = getattr(app.state, "settings")
    with DBSession(engine) as session:
        skill_service = SkillService(session, settings)
        return cast(
            list[dict[str, object]],
            [record.model_dump(mode="json") for record in skill_service.rescan_skills()],
        )


def _post_chat_in_thread(
    client: TestClient,
    session_id: str,
    payload: dict[str, object],
) -> tuple[dict[str, Response | None], threading.Thread]:
    response: dict[str, Response | None] = {"value": None}

    def run_chat_request() -> None:
        response["value"] = client.post(f"/api/sessions/{session_id}/chat", json=payload)

    worker = threading.Thread(target=run_chat_request)
    worker.start()
    return response, worker


def _write_skill(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")
