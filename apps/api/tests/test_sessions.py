import asyncio
import threading
from contextlib import ExitStack
from pathlib import Path
from typing import cast

from fastapi.testclient import TestClient
from httpx import Response
from pytest import MonkeyPatch

from app.compat.skills.models import SkillScanRoot
from app.db.models import CompatibilityScope, CompatibilitySource
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


def test_websocket_streams_session_events(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Streaming Session"})
    session_id = api_data(session_response)["id"]

    with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
        chat_response_box, worker = _post_chat_in_thread(
            client,
            session_id,
            {"content": "hello websocket", "attachments": []},
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
    assert event_types[:5] == [
        "session.updated",
        "message.created",
        "message.created",
        "generation.started",
        "assistant.summary",
    ]
    assert event_types[-1] == "session.updated"
    assert "message.updated" in event_types
    assert saw_partial_update is True
    assert [events[1]["payload"].get("role"), events[2]["payload"].get("role")] == [
        "user",
        "assistant",
    ]
    assert isinstance(events[1]["payload"].get("created_at"), str)
    assert events[2]["payload"]["content"] == ""
    assert events[3]["payload"]["user_message_id"] == events[1]["payload"]["message_id"]
    assert events[3]["payload"]["message_id"] == events[2]["payload"]["message_id"]
    assert events[4]["payload"] == {
        "message_id": events[2]["payload"]["message_id"],
        "summary": "Assistant is analyzing the request and preparing a response.",
    }
    assert (
        events[-2]["payload"]["content"] == "Test assistant reply: hello websocket (0 attachments)"
    )


def test_cancel_session_interrupts_active_generation(client: TestClient) -> None:
    class SlowStreamingChatRuntime:
        async def generate_reply(
            self,
            content: str,
            attachments: list[object],
            available_skills: list[object] | None = None,
            skill_context_prompt: str | None = None,
            execute_tool: ToolExecutor | None = None,
            callbacks: GenerationCallbacks | None = None,
        ) -> str:
            del attachments, available_skills, skill_context_prompt, execute_tool
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
                {"content": "cancel me", "attachments": []},
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
            available_skills: list[object] | None = None,
            skill_context_prompt: str | None = None,
            execute_tool: ToolExecutor | None = None,
            callbacks: GenerationCallbacks | None = None,
        ) -> str:
            del attachments, available_skills, skill_context_prompt, execute_tool
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
                {"content": "first", "attachments": []},
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
                        {"content": "second", "attachments": []},
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
        assert any(
            event["type"] == "session.updated" and event["payload"].get("queued_prompt_count") == 1
            for event in events
        )
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
            available_skills: list[object] | None = None,
            skill_context_prompt: str | None = None,
            execute_tool: ToolExecutor | None = None,
            callbacks: GenerationCallbacks | None = None,
        ) -> str:
            del content, attachments, available_skills, skill_context_prompt, callbacks
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
                json={"content": "请自动执行工具", "attachments": []},
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
        assert event_types[:5] == [
            "session.updated",
            "message.created",
            "message.created",
            "generation.started",
            "assistant.summary",
        ]
        assert event_types[-1] == "session.updated"
        assert "tool.call.started" in event_types
        assert "tool.call.finished" in event_types
        assert "message.updated" in event_types

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

        runtime_status_response = client.get("/api/runtime/status")
        assert runtime_status_response.status_code == 200
        runtime_payload = api_data(runtime_status_response)
        assert runtime_payload["recent_runs"][0]["session_id"] == session_id
        assert (
            runtime_payload["recent_runs"][0]["artifacts"][0]["relative_path"] == "reports/auto.txt"
        )
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override


def test_chat_failure_marks_session_error(client: TestClient) -> None:
    class FailingChatRuntime:
        async def generate_reply(
            self,
            content: str,
            attachments: list[object],
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
            raise ChatRuntimeError("LLM request timed out.")

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: FailingChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Failure Session"})
        session_id = api_data(session_response)["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "hello failure", "attachments": []},
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
            available_skills: list[object] | None = None,
            skill_context_prompt: str | None = None,
            execute_tool: ToolExecutor | None = None,
            callbacks: GenerationCallbacks | None = None,
        ) -> str:
            del content, attachments, skill_context_prompt, callbacks
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
            json={"content": "列出所有 skill", "attachments": []},
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
            available_skills: list[object] | None = None,
            skill_context_prompt: str | None = None,
            execute_tool: ToolExecutor | None = None,
            callbacks: GenerationCallbacks | None = None,
        ) -> str:
            del content, attachments, available_skills, skill_context_prompt, callbacks
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
            json={"content": "查看 adscan skill", "attachments": []},
        )

        assert chat_response.status_code == 200
        assert api_data(chat_response)["assistant_message"]["content"] == "adscan: ---"
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
