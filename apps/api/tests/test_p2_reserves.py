from __future__ import annotations

from pathlib import Path

from pytest import MonkeyPatch
from sqlalchemy import JSON
from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from app.core.settings import Settings
from app.db.models import (
    RuntimeExecuteRequest,
    RuntimeExecutionRunRead,
    RuntimePolicy,
)
from app.db.repositories import RunLogRepository, RuntimeRepository
from app.services.workflow_queue import (
    RedisWorkflowQueueBackend,
    execute_runtime_command,
    get_workflow_queue_backend,
)


def test_sqlmodel_json_columns_are_dialect_neutral() -> None:
    session_table = SQLModel.metadata.tables["session"]
    project_settings_table = SQLModel.metadata.tables["project_settings"]

    assert isinstance(session_table.c.runtime_policy_json.type, JSON)
    assert isinstance(project_settings_table.c.runtime_defaults.type, JSON)


def test_queue_backend_factory_exposes_redis_reserve_mode(
    tmp_path: Path,
    test_settings: Settings,
    monkeypatch: MonkeyPatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'queue-factory.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    db_session = DBSession(engine)
    test_settings.queue_backend = "redis"
    monkeypatch.setattr(
        "app.services.workflow_queue.get_runtime_backend",
        lambda _settings: object(),
    )
    try:
        backend = get_workflow_queue_backend(
            test_settings,
            RuntimeRepository(db_session),
            RunLogRepository(db_session),
        )
        assert isinstance(backend, RedisWorkflowQueueBackend)
    finally:
        db_session.close()


class _RecordingQueueBackend:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def execute(
        self,
        payload: RuntimeExecuteRequest,
        runtime_policy: RuntimePolicy | None = None,
    ) -> RuntimeExecutionRunRead:
        self.calls.append(
            (payload.command, None if runtime_policy is None else runtime_policy.model_dump_json())
        )
        raise RuntimeError("queue invoked")


def test_execute_runtime_command_routes_through_queue_backend(
    tmp_path: Path,
    test_settings: Settings,
) -> None:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'queue-routing.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    db_session = DBSession(engine)
    queue_backend = _RecordingQueueBackend()
    try:
        execute_runtime_command(
            queue_backend,
            RuntimeExecuteRequest(command="pwd"),
        )
    except RuntimeError as exc:
        assert str(exc) == "queue invoked"
    finally:
        db_session.close()

    assert queue_backend.calls == [("pwd", None)]
