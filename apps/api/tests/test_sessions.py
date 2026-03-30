from contextlib import ExitStack

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.db.repositories import SessionRepository
from app.db.session import get_db_session
from app.main import app
from app.services.chat_runtime import (
    ChatRuntimeError,
    ToolCallRequest,
    ToolExecutor,
    get_chat_runtime,
)


def test_session_lifecycle_and_history(client: TestClient) -> None:
    create_response = client.post("/api/sessions", json={"title": "Initial Session"})

    assert create_response.status_code == 201
    created_session = create_response.json()
    session_id = created_session["id"]
    assert created_session["title"] == "Initial Session"
    assert created_session["status"] == "idle"

    list_response = client.get("/api/sessions")

    assert list_response.status_code == 200
    assert [session["id"] for session in list_response.json()] == [session_id]

    detail_response = client.get(f"/api/sessions/{session_id}")

    assert detail_response.status_code == 200
    assert detail_response.json()["messages"] == []

    rename_response = client.patch(
        f"/api/sessions/{session_id}",
        json={"title": "Renamed Session", "status": "paused"},
    )

    assert rename_response.status_code == 200
    assert rename_response.json()["title"] == "Renamed Session"
    assert rename_response.json()["status"] == "paused"

    delete_response = client.delete(f"/api/sessions/{session_id}")

    assert delete_response.status_code == 204
    assert client.get(f"/api/sessions/{session_id}").status_code == 404
    assert client.get("/api/sessions").json() == []

    restore_response = client.post(f"/api/sessions/{session_id}/restore")

    assert restore_response.status_code == 200
    assert restore_response.json()["id"] == session_id
    assert restore_response.json()["deleted_at"] is None

    restored_detail_response = client.get(f"/api/sessions/{session_id}")

    assert restored_detail_response.status_code == 200
    assert restored_detail_response.json()["title"] == "Renamed Session"


def test_chat_persists_messages_and_attachments(client: TestClient) -> None:
    session_response = client.post("/api/sessions", json={"title": "Chat Session"})
    session_id = session_response.json()["id"]

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
    chat_payload = chat_response.json()
    assert chat_payload["session"]["status"] == "done"
    assert chat_payload["user_message"]["role"] == "user"
    assert chat_payload["assistant_message"]["role"] == "assistant"
    assert chat_payload["assistant_message"]["content"] == (
        "Test assistant reply: investigate target scope (1 attachments)"
    )

    detail_response = client.get(f"/api/sessions/{session_id}")

    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
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
    session_id = session_response.json()["id"]

    with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "hello websocket", "attachments": []},
        )

        assert chat_response.status_code == 200

        events = []
        while True:
            event = websocket.receive_json()
            events.append(event)

            if event["type"] == "session.updated" and event["payload"].get("status") == "done":
                break

    event_types = [event["type"] for event in events]
    assert event_types[:3] == ["session.updated", "message.created", "message.created"]
    assert event_types[-1] == "session.updated"
    assert "message.updated" in event_types
    assert [events[1]["payload"].get("role"), events[2]["payload"].get("role")] == [
        "user",
        "assistant",
    ]
    assert isinstance(events[1]["payload"].get("created_at"), str)
    assert events[2]["payload"]["content"] == ""
    assert (
        events[-2]["payload"]["content"] == "Test assistant reply: hello websocket (0 attachments)"
    )


def test_websocket_releases_db_session_after_initial_lookup(
    client: TestClient, monkeypatch: MonkeyPatch
) -> None:
    class TrackingSession:
        def __init__(self) -> None:
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1

    tracking_session = TrackingSession()
    original_override = app.dependency_overrides[get_db_session]

    def override_db_session() -> TrackingSession:
        return tracking_session

    def fake_get_session(
        self: SessionRepository, session_id: str, *, include_deleted: bool = False
    ) -> object:
        del self, session_id, include_deleted
        return object()

    app.dependency_overrides[get_db_session] = override_db_session
    monkeypatch.setattr(SessionRepository, "get_session", fake_get_session)

    try:
        with ExitStack() as stack:
            stack.enter_context(client.websocket_connect("/api/sessions/test-session/events"))
            assert tracking_session.close_calls == 1
    finally:
        app.dependency_overrides[get_db_session] = original_override


def test_chat_can_auto_call_runtime_tools(client: TestClient) -> None:
    class ToolCallingChatRuntime:
        async def generate_reply(
            self,
            content: str,
            attachments: list[object],
            execute_tool: ToolExecutor,
        ) -> str:
            del content, attachments
            tool_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="tool-call-1",
                    command="printf 'auto tool' > reports/auto.txt",
                    timeout_seconds=10,
                    artifact_paths=["reports/auto.txt"],
                )
            )
            return f"工具执行完成，状态：{tool_result.status}。"

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: ToolCallingChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Auto Tool Session"})
        session_id = session_response.json()["id"]

        with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
            chat_response = client.post(
                f"/api/sessions/{session_id}/chat",
                json={"content": "请自动执行工具", "attachments": []},
            )

            assert chat_response.status_code == 200
            assert (
                chat_response.json()["assistant_message"]["content"]
                == "工具执行完成，状态：success。"
            )

            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)

                if event["type"] == "session.updated" and event["payload"].get("status") == "done":
                    break

        event_types = [event["type"] for event in events]
        assert event_types[:2] == ["session.updated", "message.created"]
        assert event_types[-1] == "session.updated"
        assert "tool.call.started" in event_types
        assert "tool.call.finished" in event_types
        assert "message.updated" in event_types

        started_index = event_types.index("tool.call.started")
        finished_index = event_types.index("tool.call.finished")
        assistant_created_index = event_types.index("message.created", finished_index + 1)
        last_message_update_index = max(
            index for index, event_type in enumerate(event_types) if event_type == "message.updated"
        )

        assert events[started_index]["payload"] == {
            "tool": "shell",
            "tool_call_id": "tool-call-1",
            "command": "printf 'auto tool' > reports/auto.txt",
            "timeout_seconds": 10,
            "artifact_paths": ["reports/auto.txt"],
        }
        assert events[finished_index]["payload"]["tool"] == "shell"
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
        assert events[assistant_created_index]["payload"]["role"] == "assistant"
        assert events[assistant_created_index]["payload"]["content"] == ""
        assert (
            events[last_message_update_index]["payload"]["content"]
            == "工具执行完成，状态：success。"
        )

        runtime_status_response = client.get("/api/runtime/status")
        assert runtime_status_response.status_code == 200
        runtime_payload = runtime_status_response.json()
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
            execute_tool: ToolExecutor | None = None,
        ) -> str:
            del content, attachments, execute_tool
            raise ChatRuntimeError("LLM request timed out.")

    original_override = app.dependency_overrides[get_chat_runtime]
    app.dependency_overrides[get_chat_runtime] = lambda: FailingChatRuntime()

    try:
        session_response = client.post("/api/sessions", json={"title": "Failure Session"})
        session_id = session_response.json()["id"]

        chat_response = client.post(
            f"/api/sessions/{session_id}/chat",
            json={"content": "hello failure", "attachments": []},
        )

        assert chat_response.status_code == 502
        assert chat_response.json()["detail"] == "LLM request timed out."

        detail_response = client.get(f"/api/sessions/{session_id}")
        assert detail_response.status_code == 200
        detail_payload = detail_response.json()
        assert detail_payload["status"] == "error"
        assert [message["role"] for message in detail_payload["messages"]] == ["user"]
    finally:
        app.dependency_overrides[get_chat_runtime] = original_override
