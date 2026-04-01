from __future__ import annotations

import logging
from collections.abc import Generator
from pathlib import Path

from fastapi import Request, WebSocket
from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.core.settings import get_settings
from app.db.repositories import RunLogRepository

logger = logging.getLogger("aegissec.api")


def _sqlite_connect_args(database_url: str) -> dict[str, bool]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}

    return {}


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    if not database_url.startswith("sqlite:///"):
        return

    raw_path = database_url.removeprefix("sqlite:///")
    if raw_path == ":memory:" or raw_path.startswith("./"):
        return

    Path(raw_path).parent.mkdir(parents=True, exist_ok=True)


settings = get_settings()
_ensure_sqlite_parent_dir(settings.database_url)
engine = create_engine(
    settings.database_url, connect_args=_sqlite_connect_args(settings.database_url)
)

_PHASE6_SQLITE_TABLES = ("graph_edge", "graph_node", "task_node", "workflow_run")
_PHASE6_REQUIRED_COLUMNS: dict[str, set[str]] = {
    "workflow_run": {"state"},
    "graph_node": {"workflow_run_id"},
    "graph_edge": {"workflow_run_id"},
}
_SQLITE_ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "session": {
        "project_id": "TEXT",
        "goal": "TEXT",
        "scenario_type": "TEXT",
        "current_phase": "TEXT",
        "runtime_policy_json": "JSON",
        "runtime_profile_name": "TEXT",
    },
    "skill_record": {
        "parameter_schema": "JSON NOT NULL DEFAULT '{}'",
        "enabled": "BOOLEAN NOT NULL DEFAULT 1",
    },
    "mcp_server": {
        "health_status": "TEXT",
        "health_latency_ms": "INTEGER",
        "health_error": "TEXT",
        "health_checked_at": "DATETIME",
    },
}


def _is_sqlite_engine() -> bool:
    return str(engine.url).startswith("sqlite")


def _phase6_schema_requires_rebuild() -> bool:
    inspector = inspect(engine)
    for table_name, required_columns in _PHASE6_REQUIRED_COLUMNS.items():
        if not inspector.has_table(table_name):
            continue
        existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
        if not required_columns.issubset(existing_columns):
            return True
    return False


def _rebuild_phase6_sqlite_tables() -> None:
    with engine.begin() as connection:
        for table_name in _PHASE6_SQLITE_TABLES:
            connection.execute(text(f"DROP TABLE IF EXISTS {table_name}"))


def _ensure_sqlite_additive_columns() -> None:
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table_name, columns in _SQLITE_ADDITIVE_COLUMNS.items():
            if not inspector.has_table(table_name):
                continue

            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_definition in columns.items():
                if column_name in existing_columns:
                    continue
                connection.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")
                )


def init_db() -> None:
    if _is_sqlite_engine() and _phase6_schema_requires_rebuild():
        _rebuild_phase6_sqlite_tables()
    SQLModel.metadata.create_all(engine)
    if _is_sqlite_engine():
        _ensure_sqlite_additive_columns()


def persist_request_log(session: Session, request: Request) -> None:
    pending_request_log = getattr(request.state, "pending_request_log", None)
    if not isinstance(pending_request_log, dict):
        return

    raw_session_id = pending_request_log.get("session_id")
    session_id = raw_session_id if isinstance(raw_session_id, str) else None
    raw_payload = pending_request_log.get("payload")
    payload = raw_payload if isinstance(raw_payload, dict) else {}

    try:
        repository = RunLogRepository(session)
        repository.create_log(
            session_id=session_id,
            level=str(pending_request_log.get("level", "info")),
            source=str(pending_request_log.get("source", "api")),
            event_type=str(pending_request_log.get("event_type", "request.completed")),
            message=str(pending_request_log.get("message", "request completed")),
            payload=payload,
            commit=False,
        )
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Failed to persist request log")
    finally:
        request.state.pending_request_log = None


def get_db_session(request: Request) -> Generator[Session, None, None]:
    with Session(engine) as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            persist_request_log(session, request)


def get_websocket_db_session(websocket: WebSocket) -> Generator[Session, None, None]:
    del websocket
    with Session(engine) as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
