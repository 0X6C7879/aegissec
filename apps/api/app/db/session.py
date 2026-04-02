from __future__ import annotations

import logging
from collections.abc import Generator
from datetime import UTC, datetime
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
        "active_branch_id": "TEXT",
    },
    "conversation_branch": {
        "parent_branch_id": "TEXT",
        "forked_from_message_id": "TEXT",
        "name": "TEXT NOT NULL DEFAULT 'Main'",
        "created_at": "DATETIME",
        "updated_at": "DATETIME",
    },
    "message": {
        "parent_message_id": "TEXT",
        "branch_id": "TEXT",
        "generation_id": "TEXT",
        "status": "TEXT NOT NULL DEFAULT 'COMPLETED'",
        "message_kind": "TEXT NOT NULL DEFAULT 'MESSAGE'",
        "sequence": "INTEGER NOT NULL DEFAULT 0",
        "turn_index": "INTEGER NOT NULL DEFAULT 0",
        "edited_from_message_id": "TEXT",
        "version_group_id": "TEXT",
        "metadata": "JSON NOT NULL DEFAULT '{}'",
        "assistant_transcript": "JSON NOT NULL DEFAULT '[]'",
        "error_message": "TEXT",
        "completed_at": "DATETIME",
    },
    "chat_generation": {
        "action": "TEXT NOT NULL DEFAULT 'REPLY'",
        "target_message_id": "TEXT",
        "reasoning_summary": "TEXT",
        "reasoning_trace": "JSON NOT NULL DEFAULT '[]'",
        "cancel_requested_at": "DATETIME",
        "worker_id": "TEXT",
        "lease_claimed_at": "DATETIME",
        "lease_expires_at": "DATETIME",
        "attempt_count": "INTEGER NOT NULL DEFAULT 0",
    },
    "generation_step": {
        "status": "TEXT NOT NULL DEFAULT 'pending'",
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
_SQLITE_INDEX_STATEMENTS = (
    "CREATE INDEX IF NOT EXISTS ix_session_active_branch_id ON session (active_branch_id)",
    (
        "CREATE INDEX IF NOT EXISTS ix_conversation_branch_session_id "
        "ON conversation_branch (session_id)"
    ),
    "CREATE INDEX IF NOT EXISTS ix_message_branch_id ON message (branch_id)",
    "CREATE INDEX IF NOT EXISTS ix_message_generation_id ON message (generation_id)",
    "CREATE INDEX IF NOT EXISTS ix_message_status ON message (status)",
    "CREATE INDEX IF NOT EXISTS ix_message_message_kind ON message (message_kind)",
    "CREATE INDEX IF NOT EXISTS ix_message_sequence ON message (sequence)",
    "CREATE INDEX IF NOT EXISTS ix_message_turn_index ON message (turn_index)",
    "CREATE INDEX IF NOT EXISTS ix_message_version_group_id ON message (version_group_id)",
    "CREATE INDEX IF NOT EXISTS ix_chat_generation_session_id ON chat_generation (session_id)",
    "CREATE INDEX IF NOT EXISTS ix_chat_generation_branch_id ON chat_generation (branch_id)",
    (
        "CREATE INDEX IF NOT EXISTS ix_chat_generation_assistant_message_id "
        "ON chat_generation (assistant_message_id)"
    ),
    "CREATE INDEX IF NOT EXISTS ix_chat_generation_status ON chat_generation (status)",
    "CREATE INDEX IF NOT EXISTS ix_chat_generation_created_at ON chat_generation (created_at)",
    "CREATE INDEX IF NOT EXISTS ix_chat_generation_worker_id ON chat_generation (worker_id)",
    (
        "CREATE INDEX IF NOT EXISTS ix_chat_generation_lease_expires_at "
        "ON chat_generation (lease_expires_at)"
    ),
    "CREATE INDEX IF NOT EXISTS ix_generation_step_generation_id ON generation_step (generation_id)",
    "CREATE INDEX IF NOT EXISTS ix_generation_step_session_id ON generation_step (session_id)",
    "CREATE INDEX IF NOT EXISTS ix_generation_step_message_id ON generation_step (message_id)",
    "CREATE INDEX IF NOT EXISTS ix_generation_step_sequence ON generation_step (sequence)",
    "CREATE INDEX IF NOT EXISTS ix_generation_step_kind ON generation_step (kind)",
    "CREATE INDEX IF NOT EXISTS ix_generation_step_phase ON generation_step (phase)",
    "CREATE INDEX IF NOT EXISTS ix_generation_step_status ON generation_step (status)",
    "CREATE INDEX IF NOT EXISTS ix_generation_step_state ON generation_step (state)",
    "CREATE INDEX IF NOT EXISTS ix_generation_step_tool_name ON generation_step (tool_name)",
    "CREATE INDEX IF NOT EXISTS ix_generation_step_tool_call_id ON generation_step (tool_call_id)",
    "CREATE INDEX IF NOT EXISTS ix_generation_step_started_at ON generation_step (started_at)",
    "CREATE INDEX IF NOT EXISTS ix_session_event_log_session_id ON session_event_log (session_id)",
    "CREATE INDEX IF NOT EXISTS ix_session_event_log_event_type ON session_event_log (event_type)",
    "CREATE INDEX IF NOT EXISTS ix_session_event_log_timestamp ON session_event_log (timestamp)",
)


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


def _ensure_sqlite_indexes() -> None:
    with engine.begin() as connection:
        for statement in _SQLITE_INDEX_STATEMENTS:
            connection.execute(text(statement))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _backfill_sqlite_chat_core_state() -> None:
    inspector = inspect(engine)
    required_tables = {"session", "message", "conversation_branch"}
    if not required_tables.issubset(set(inspector.get_table_names())):
        return

    session_columns = {column["name"] for column in inspector.get_columns("session")}
    message_columns = {column["name"] for column in inspector.get_columns("message")}
    if "active_branch_id" not in session_columns or "branch_id" not in message_columns:
        return

    with engine.begin() as connection:
        session_rows = connection.execute(
            text("SELECT id, created_at, updated_at, active_branch_id FROM session")
        ).mappings()

        for session_row in session_rows:
            session_id = str(session_row["id"])
            created_at = session_row["created_at"] or _utc_now()
            updated_at = session_row["updated_at"] or created_at
            active_branch_id = session_row["active_branch_id"]

            branch_exists = connection.execute(
                text("SELECT 1 FROM conversation_branch WHERE id = :branch_id LIMIT 1"),
                {"branch_id": session_id},
            ).first()
            if branch_exists is None:
                connection.execute(
                    text(
                        """
                        INSERT INTO conversation_branch (
                            id,
                            session_id,
                            parent_branch_id,
                            forked_from_message_id,
                            name,
                            created_at,
                            updated_at
                        )
                        VALUES (
                            :id,
                            :session_id,
                            NULL,
                            NULL,
                            'Main',
                            :created_at,
                            :updated_at
                        )
                        """
                    ),
                    {
                        "id": session_id,
                        "session_id": session_id,
                        "created_at": created_at,
                        "updated_at": updated_at,
                    },
                )

            active_branch_exists = (
                connection.execute(
                    text("SELECT 1 FROM conversation_branch WHERE id = :branch_id LIMIT 1"),
                    {"branch_id": active_branch_id},
                ).first()
                if active_branch_id is not None
                else None
            )
            if active_branch_id is None or active_branch_exists is None:
                connection.execute(
                    text(
                        """
                        UPDATE session
                        SET active_branch_id = :branch_id,
                            updated_at = :updated_at
                        WHERE id = :session_id
                        """
                    ),
                    {
                        "branch_id": session_id,
                        "updated_at": updated_at,
                        "session_id": session_id,
                    },
                )

            message_rows = (
                connection.execute(
                    text(
                        """
                    SELECT id, role, created_at, branch_id
                    FROM message
                    WHERE session_id = :session_id
                    ORDER BY created_at ASC, id ASC
                    """
                    ),
                    {"session_id": session_id},
                )
                .mappings()
                .all()
            )
            if not message_rows or all(row["branch_id"] is not None for row in message_rows):
                continue

            parent_message_id: str | None = None
            turn_index = 0
            for sequence, message_row in enumerate(message_rows, start=1):
                role = str(message_row["role"]).lower()
                if role == "user":
                    turn_index += 1
                elif turn_index == 0:
                    turn_index = 1

                connection.execute(
                    text(
                        """
                        UPDATE message
                        SET parent_message_id = :parent_message_id,
                            branch_id = :branch_id,
                            status = COALESCE(status, 'COMPLETED'),
                            message_kind = COALESCE(message_kind, 'MESSAGE'),
                            sequence = :sequence,
                            turn_index = :turn_index,
                            version_group_id = COALESCE(version_group_id, :version_group_id),
                            metadata = COALESCE(metadata, '{}'),
                            completed_at = COALESCE(completed_at, created_at)
                        WHERE id = :message_id
                        """
                    ),
                    {
                        "parent_message_id": parent_message_id,
                        "branch_id": session_id,
                        "sequence": sequence,
                        "turn_index": turn_index,
                        "version_group_id": str(message_row["id"]),
                        "message_id": str(message_row["id"]),
                    },
                )
                parent_message_id = str(message_row["id"])


def init_db() -> None:
    if _is_sqlite_engine() and _phase6_schema_requires_rebuild():
        _rebuild_phase6_sqlite_tables()
    SQLModel.metadata.create_all(engine)
    if _is_sqlite_engine():
        _ensure_sqlite_additive_columns()
        _ensure_sqlite_indexes()
        _backfill_sqlite_chat_core_state()


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
