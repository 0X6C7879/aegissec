from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import Request, WebSocket
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.core.events import SessionEventBroker, get_event_broker
from app.core.settings import Settings, get_settings
from app.db.models import ExecutionStatus, RuntimeContainerStatus
from app.db.session import get_db_session, get_websocket_db_session, persist_request_log
from app.main import app
from app.services.chat_runtime import GenerationCallbacks, get_chat_runtime
from app.services.runtime import (
    RuntimeCommandResult,
    RuntimeContainerState,
    get_runtime_backend,
)
from app.services.terminal_runtime import (
    LiveTerminalJobRegistry,
    LiveTerminalRegistry,
    TerminalBackendEvent,
    TerminalProcess,
)


class FakeChatRuntime:
    async def generate_reply(
        self,
        content: str,
        attachments: list[object],
        conversation_messages: list[object] | None = None,
        available_skills: list[object] | None = None,
        skill_context_prompt: str | None = None,
        execute_tool: object | None = None,
        callbacks: GenerationCallbacks | None = None,
    ) -> str:
        del conversation_messages, available_skills, skill_context_prompt, execute_tool
        normalized_content = " ".join(content.split())
        reply = f"Test assistant reply: {normalized_content} ({len(attachments)} attachments)"
        if callbacks is not None and callbacks.on_text_delta is not None:
            midpoint = max(1, len(reply) // 2)
            await callbacks.on_text_delta(reply[:midpoint])
            await callbacks.on_text_delta(reply[midpoint:])
        return reply


class FakeRuntimeBackend:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._workspace_dir = Path(settings.runtime_workspace_dir).resolve()
        self._workspace_dir.mkdir(parents=True, exist_ok=True)
        self._container_id = "fake-runtime-container"
        self._exists = False
        self._running = False
        self._queued_results: list[RuntimeCommandResult] = []

    def queue_result(
        self,
        *,
        status: ExecutionStatus,
        exit_code: int | None,
        stdout: str,
        stderr: str,
    ) -> None:
        now = datetime.now(UTC)
        self._queued_results.append(
            RuntimeCommandResult(
                status=status,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                started_at=now,
                ended_at=now,
                container_state=self._state(RuntimeContainerStatus.RUNNING),
            )
        )

    def inspect(self) -> RuntimeContainerState:
        if not self._exists:
            return self._state(RuntimeContainerStatus.MISSING, container_id=None)
        if self._running:
            return self._state(RuntimeContainerStatus.RUNNING)
        return self._state(RuntimeContainerStatus.STOPPED)

    def ensure_started(self) -> RuntimeContainerState:
        self._exists = True
        self._running = True
        return self._state(RuntimeContainerStatus.RUNNING)

    def stop(self) -> RuntimeContainerState:
        if not self._exists:
            return self._state(RuntimeContainerStatus.MISSING, container_id=None)
        self._running = False
        return self._state(RuntimeContainerStatus.STOPPED)

    def execute(
        self,
        command: str,
        timeout_seconds: int,
        artifact_paths: list[str],
    ) -> RuntimeCommandResult:
        del command, timeout_seconds

        self.ensure_started()
        for artifact_path in artifact_paths:
            self._materialize_artifact(artifact_path)

        if self._queued_results:
            result = self._queued_results.pop(0)
            result.container_state = self._state(RuntimeContainerStatus.RUNNING)
            return result

        now = datetime.now(UTC)
        return RuntimeCommandResult(
            status=ExecutionStatus.SUCCESS,
            exit_code=0,
            stdout="runtime command completed",
            stderr="",
            started_at=now,
            ended_at=now,
            container_state=self._state(RuntimeContainerStatus.RUNNING),
        )

    def _materialize_artifact(self, artifact_path: str) -> None:
        normalized_path = artifact_path.replace("\\", "/")
        workspace_prefix = self._settings.runtime_workspace_container_path.rstrip("/")
        if normalized_path == workspace_prefix or normalized_path.startswith(
            f"{workspace_prefix}/"
        ):
            normalized_path = normalized_path.removeprefix(workspace_prefix).lstrip("/")

        artifact_file = (self._workspace_dir / Path(normalized_path)).resolve()
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        artifact_file.write_text("artifact", encoding="utf-8")

    def _state(
        self,
        status: RuntimeContainerStatus,
        *,
        container_id: str | None = "fake-runtime-container",
    ) -> RuntimeContainerState:
        return RuntimeContainerState(
            status=status,
            container_name=self._settings.runtime_container_name,
            image=self._settings.kali_image,
            workspace_host_path=str(self._workspace_dir),
            workspace_container_path=self._settings.runtime_workspace_container_path,
            container_id=container_id,
            started_at=datetime.now(UTC) if status == RuntimeContainerStatus.RUNNING else None,
        )


class FakeTerminalProcess(TerminalProcess):
    def __init__(self, *, terminal_id: str, cols: int, rows: int) -> None:
        self.terminal_id = terminal_id
        self.cols = cols
        self.rows = rows
        self.events: asyncio.Queue[TerminalBackendEvent] = asyncio.Queue()
        self.inputs: list[bytes] = []
        self.resize_history: list[tuple[int, int]] = [(cols, rows)]
        self.signals: list[str] = []
        self.closed_reasons: list[str] = []
        self.closed = False

    async def send_input(self, data: bytes) -> None:
        self.inputs.append(data)
        await self.events.put(TerminalBackendEvent.output(data=data))
        if data.endswith(b"exit\n"):
            await self.events.put(TerminalBackendEvent.exit(exit_code=0, reason="exit"))

    async def resize(self, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows
        self.resize_history.append((cols, rows))

    async def send_signal(self, signal_name: str) -> None:
        self.signals.append(signal_name)
        if signal_name.lower() in {"interrupt", "int"}:
            await self.events.put(TerminalBackendEvent.output(data=b"^C"))

    async def send_eof(self) -> None:
        await self.events.put(TerminalBackendEvent.exit(exit_code=0, reason="eof"))

    async def close(self, *, reason: str) -> None:
        self.closed = True
        self.closed_reasons.append(reason)
        await self.events.put(TerminalBackendEvent.exit(exit_code=None, reason=reason))


class FakeTerminalBackend:
    def __init__(self) -> None:
        self.processes: dict[str, FakeTerminalProcess] = {}

    async def open_terminal(
        self,
        *,
        terminal_id: str,
        shell: str,
        cwd: str,
        cols: int,
        rows: int,
    ) -> TerminalProcess:
        del shell, cwd
        process = FakeTerminalProcess(terminal_id=terminal_id, cols=cols, rows=rows)
        self.processes[terminal_id] = process
        return process

    async def shutdown(self) -> None:
        self.processes.clear()


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    database_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    settings = Settings.model_validate(
        {
            "app_name": "aegissec",
            "app_version": "0.1.0",
            "api_host": "127.0.0.1",
            "api_port": 8000,
            "frontend_origin": "http://127.0.0.1:5173",
            "kali_image": "aegissec-kali:latest",
            "runtime_container_name": "aegissec-kali-runtime",
            "runtime_workspace_dir": str((tmp_path / "runtime-workspace").resolve()),
            "runtime_workspace_container_path": "/workspace",
            "runtime_default_timeout_seconds": 1200,
            "runtime_recent_runs_limit": 10,
            "runtime_recent_artifacts_limit": 20,
            "terminal_disconnect_grace_seconds": 0.05,
            "mcp_import_paths": [],
            "database_url": database_url,
            "llm_api_key": None,
            "llm_api_base_url": None,
            "llm_default_model": None,
        }
    )
    settings.llm_provider = "openai"
    settings.anthropic_api_key = None
    settings.anthropic_api_base_url = None
    settings.anthropic_model = None
    return settings


@pytest.fixture
def runtime_backend(test_settings: Settings) -> FakeRuntimeBackend:
    return FakeRuntimeBackend(test_settings)


@pytest.fixture
def terminal_backend() -> FakeTerminalBackend:
    return FakeTerminalBackend()


@pytest.fixture
def client(
    tmp_path: Path,
    test_settings: Settings,
    runtime_backend: FakeRuntimeBackend,
    terminal_backend: FakeTerminalBackend,
) -> Generator[TestClient, None, None]:
    database_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    event_broker = SessionEventBroker()
    event_broker.configure_persistence(lambda: Session(engine))

    def override_db_session(request: Request) -> Generator[Session, None, None]:
        with Session(engine) as session:
            try:
                yield session
            except Exception:
                session.rollback()
                raise
            finally:
                persist_request_log(session, request)

    def override_websocket_db_session(websocket: WebSocket) -> Generator[Session, None, None]:
        del websocket
        with Session(engine) as session:
            try:
                yield session
            except Exception:
                session.rollback()
                raise

    def override_event_broker() -> SessionEventBroker:
        return event_broker

    def override_chat_runtime() -> FakeChatRuntime:
        return FakeChatRuntime()

    def override_settings() -> Settings:
        return test_settings

    def override_runtime_backend() -> FakeRuntimeBackend:
        return runtime_backend

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_websocket_db_session] = override_websocket_db_session
    app.dependency_overrides[get_event_broker] = override_event_broker
    app.dependency_overrides[get_chat_runtime] = override_chat_runtime
    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_runtime_backend] = override_runtime_backend
    app.state.database_engine = engine
    app.state.settings = test_settings
    app.state.live_terminal_registry = LiveTerminalRegistry()
    app.state.live_terminal_job_registry = LiveTerminalJobRegistry()
    app.state.terminal_backend = terminal_backend

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
