from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from app.core.events import SessionEventBroker, get_event_broker
from app.core.settings import Settings, get_settings
from app.db.models import ExecutionStatus, RuntimeContainerStatus
from app.db.session import get_db_session
from app.main import app
from app.services.chat_runtime import get_chat_runtime
from app.services.runtime import (
    RuntimeCommandResult,
    RuntimeContainerState,
    get_runtime_backend,
)


class FakeChatRuntime:
    async def generate_reply(
        self,
        content: str,
        attachments: list[object],
        execute_tool: object | None = None,
    ) -> str:
        del execute_tool
        normalized_content = " ".join(content.split())
        return f"Test assistant reply: {normalized_content} ({len(attachments)} attachments)"


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


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    database_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    return Settings.model_validate(
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
            "runtime_default_timeout_seconds": 300,
            "runtime_recent_runs_limit": 10,
            "runtime_recent_artifacts_limit": 20,
            "mcp_import_paths": [],
            "database_url": database_url,
            "llm_api_key": None,
            "llm_api_base_url": None,
            "llm_default_model": None,
        }
    )


@pytest.fixture
def runtime_backend(test_settings: Settings) -> FakeRuntimeBackend:
    return FakeRuntimeBackend(test_settings)


@pytest.fixture
def client(
    tmp_path: Path,
    test_settings: Settings,
    runtime_backend: FakeRuntimeBackend,
) -> Generator[TestClient, None, None]:
    database_url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    event_broker = SessionEventBroker()

    def override_db_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session

    def override_event_broker() -> SessionEventBroker:
        return event_broker

    def override_chat_runtime() -> FakeChatRuntime:
        return FakeChatRuntime()

    def override_settings() -> Settings:
        return test_settings

    def override_runtime_backend() -> FakeRuntimeBackend:
        return runtime_backend

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_event_broker] = override_event_broker
    app.dependency_overrides[get_chat_runtime] = override_chat_runtime
    app.dependency_overrides[get_settings] = override_settings
    app.dependency_overrides[get_runtime_backend] = override_runtime_backend

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
