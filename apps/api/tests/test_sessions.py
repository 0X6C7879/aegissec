import asyncio
import threading
import time
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from pytest import MonkeyPatch
from sqlmodel import Session as DBSession
from starlette.testclient import WebSocketDenialResponse

from app.compat.skills.models import SkillScanRoot
from app.compat.skills.service import SkillContentReadError
from app.db.models import (
    CompatibilityScope,
    CompatibilitySource,
    GenerationStatus,
    MessageRole,
    MessageStatus,
)
from app.db.repositories import SessionRepository
from app.db.session import get_websocket_db_session
from app.main import app
from app.services.chat_runtime import (
    ChatRuntimeError,
    GenerationCallbacks,
    ToolCallRequest,
    ToolExecutor,
    get_chat_runtime,
)
from app.services.session_generation import recover_abandoned_generations
from tests.utils import api_data


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
            await asyncio.sleep(0.05)
            await callbacks.on_text_delta("queued ")
            await asyncio.sleep(0.05)
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

        deadline = time.time() + 3
        conversation_payload = None
        while time.time() < deadline:
            conversation_response = client.get(f"/api/sessions/{session_id}/conversation")
            conversation_payload = api_data(conversation_response)
            generations = conversation_payload["generations"]
            if generations and generations[0]["status"] == "completed":
                break
            time.sleep(0.05)

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
                await asyncio.sleep(0.05)
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

            deadline = time.time() + 2
            queue_payload = None
            while time.time() < deadline:
                queue_response = client.get(f"/api/sessions/{session_id}/queue")
                queue_payload = api_data(queue_response)
                if (
                    queue_payload["active_generation"] is not None
                    and len(queue_payload["queued_generations"]) == 1
                ):
                    break
                time.sleep(0.05)

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


def test_websocket_rejects_invalid_session_before_accept(client: TestClient) -> None:
    with pytest.raises(WebSocketDenialResponse) as exc_info:
        with client.websocket_connect("/api/sessions/nonexistent-session/events"):
            pass

    response = exc_info.value
    assert response.status_code == 404
    assert "Session not found" in response.text


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
                await asyncio.sleep(0.03)
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

        assert events[started_index]["payload"] == {
            "tool": "execute_kali_command",
            "tool_call_id": "tool-call-1",
            "arguments": {
                "command": "printf 'auto tool' > reports/auto.txt",
                "timeout_seconds": 10,
                "artifact_paths": ["reports/auto.txt"],
            },
            "command": "printf 'auto tool' > reports/auto.txt",
            "timeout_seconds": 10,
            "artifact_paths": ["reports/auto.txt"],
        }
        assert events[finished_index]["payload"]["tool"] == "execute_kali_command"
        assert events[finished_index]["payload"]["tool_call_id"] == "tool-call-1"
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
        assert events[finished_index]["payload"]["result"] == {
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
        assert any(
            any(segment["kind"] == "tool_call" for segment in payload["assistant_transcript"])
            for payload in tool_update_payloads
        )
        assert any(
            any(segment["kind"] == "tool_result" for segment in payload["assistant_transcript"])
            for payload in tool_update_payloads
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
            "status": "success",
            "exit_code": 0,
            "stdout": "runtime command completed",
            "stderr": "",
            "artifacts": ["reports/auto.txt"],
        }
        assert output_segment["text"] == "工具执行完成，状态：success。"
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
                json={"content": "请给出结果", "attachments": [], "wait_for_completion": True},
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
            assert skill_context_prompt is not None
            assert "Auto-selected skill: docx" in skill_context_prompt
            assert "# docx" in skill_context_prompt
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
        assert any(segment["text"] == "自动选择 docx" for segment in status_segments)
        assert [segment["kind"] for segment in relevant_segments[:3]] == [
            "tool_call",
            "tool_result",
            "output",
        ]
        assert relevant_segments[0]["tool_name"] == "read_skill_content"
        assert relevant_segments[1]["metadata"]["result"]["skill"]["directory_name"] == "docx"
        assert chat_payload["generation"]["metadata"]["prompt_provenance"]["autorouted_skill"] == {
            "state": "skill.autoroute.selected",
            "skill": "docx",
            "confidence": 100,
            "reason": "matched explicit skill alias 'docx'",
            "top_candidate": "docx",
            "candidates": [
                {
                    "skill": "docx",
                    "confidence": 100,
                    "reason": "matched explicit skill alias 'docx'",
                }
            ],
            "context_injected": True,
        }
        assert chat_payload["assistant_message"]["content"] == "已收到 docx 自动技能上下文"
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


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
            "ctf-web": """---
name: ctf-web
description: Web CTF exploitation playbook
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
            assert skill_context_prompt is not None
            assert "Auto-selected skill: ctf-web" in skill_context_prompt
            assert "# ctf-web" in skill_context_prompt
            return "已收到 ctf-web 自动技能上下文"

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

        assert any(event["type"] == "tool.call.started" for event in events)
        transcript = chat_payload["assistant_message"]["assistant_transcript"]
        status_segments = [segment for segment in transcript if segment["kind"] == "status"]
        relevant_segments = [
            segment
            for segment in transcript
            if segment["kind"] in {"tool_call", "tool_result", "output"}
        ]
        assert any(segment["text"] == "自动选择 ctf-web" for segment in status_segments)
        assert relevant_segments[0]["tool_name"] == "read_skill_content"
        assert relevant_segments[1]["metadata"]["result"]["skill"]["directory_name"] == "ctf-web"
        assert chat_payload["generation"]["metadata"]["prompt_provenance"]["autorouted_skill"] == {
            "state": "skill.autoroute.selected",
            "skill": "ctf-web",
            "confidence": 72,
            "reason": "matched alias tokens 'ctf web'",
            "top_candidate": "ctf-web",
            "candidates": [
                {
                    "skill": "ctf-web",
                    "confidence": 72,
                    "reason": "matched alias tokens 'ctf web'",
                }
            ],
            "context_injected": True,
        }
        assert chat_payload["assistant_message"]["content"] == "已收到 ctf-web 自动技能上下文"
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
            assert "Auto-selected skill:" not in skill_context_prompt
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
        "app.compat.skills.service.SkillService.read_skill_content_by_name_or_directory_name",
        lambda self, name_or_slug: (_ for _ in ()).throw(
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
            assert skill_context_prompt is not None
            assert "Auto-selected skill: docx" not in skill_context_prompt
            return "继续普通流程"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: FailedAutoRouteRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Autoroute Failure"})
        session_id = api_data(session_response)["id"]

        with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
            chat_response = client.post(
                f"/api/sessions/{session_id}/chat",
                json={
                    "content": "请处理这个 docx 文件",
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
        assert any(event["type"] == "tool.call.failed" for event in events)
        transcript = chat_payload["assistant_message"]["assistant_transcript"]
        autoroute_feedback_segments = [
            segment for segment in transcript if segment["kind"] in {"status", "error"}
        ]
        assert any(
            "自动预载技能失败：docx" in (segment["text"] or "")
            for segment in autoroute_feedback_segments
        )
        assert any(segment["kind"] == "error" for segment in transcript)
        assert chat_payload["assistant_message"]["content"] == "继续普通流程"
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

    rescan_response = client.post("/api/skills/rescan")
    assert rescan_response.status_code == 200
    return cast(list[dict[str, object]], rescan_response.json())


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
