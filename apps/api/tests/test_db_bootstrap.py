from pathlib import Path

from pytest import MonkeyPatch
from sqlalchemy import inspect, text
from sqlmodel import Session, create_engine

from app.db import session as db_session_module
from app.db.repositories.sessions import SessionRepository


def test_init_db_adds_missing_columns_for_existing_sqlite_tables(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "bootstrap.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE session (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE skill_record (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    root_dir TEXT NOT NULL,
                    directory_name TEXT NOT NULL,
                    entry_file TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    compatibility JSON NOT NULL,
                    metadata JSON NOT NULL,
                    raw_frontmatter JSON NOT NULL,
                    status TEXT NOT NULL,
                    error_message TEXT NULL,
                    content_hash TEXT NOT NULL,
                    last_scanned_at TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    source TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    transport TEXT NOT NULL,
                    enabled BOOLEAN NOT NULL,
                    command TEXT NULL,
                    args JSON NOT NULL,
                    env JSON NOT NULL,
                    url TEXT NULL,
                    headers JSON NOT NULL,
                    timeout_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    last_error TEXT NULL,
                    config_path TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                )
                """
            )
        )

    monkeypatch.setattr(db_session_module, "engine", engine)

    db_session_module.init_db()

    inspector = inspect(engine)
    session_columns = {column["name"] for column in inspector.get_columns("session")}
    skill_columns = {column["name"] for column in inspector.get_columns("skill_record")}
    mcp_columns = {column["name"] for column in inspector.get_columns("mcp_server")}

    assert {
        "project_id",
        "goal",
        "scenario_type",
        "current_phase",
        "runtime_policy_json",
        "runtime_profile_name",
    }.issubset(session_columns)
    assert {"parameter_schema", "enabled"}.issubset(skill_columns)
    assert {"health_status", "health_latency_ms", "health_error", "health_checked_at"}.issubset(
        mcp_columns
    )


def test_init_db_upgrades_legacy_chat_tables_for_existing_sqlite_db(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy-chat.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE session (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    deleted_at DATETIME NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE message (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    attachments JSON NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO session (id, title, status, created_at, updated_at, deleted_at)
                VALUES (
                    'session-1',
                    'Legacy Session',
                    'IDLE',
                    '2026-04-02T00:00:00+00:00',
                    '2026-04-02T00:00:00+00:00',
                    NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO message (id, session_id, role, content, attachments, created_at)
                VALUES
                    ('message-1', 'session-1', 'USER', 'hello', '[]', '2026-04-02T00:00:01+00:00'),
                    ('message-2', 'session-1', 'ASSISTANT', 'hi', '[]', '2026-04-02T00:00:02+00:00')
                """
            )
        )

    monkeypatch.setattr(db_session_module, "engine", engine)

    db_session_module.init_db()

    inspector = inspect(engine)
    assert {"conversation_branch", "chat_generation"}.issubset(set(inspector.get_table_names()))
    assert "active_branch_id" in {column["name"] for column in inspector.get_columns("session")}
    assert {
        "parent_message_id",
        "branch_id",
        "generation_id",
        "status",
        "message_kind",
        "sequence",
        "turn_index",
        "version_group_id",
        "metadata",
        "completed_at",
    }.issubset({column["name"] for column in inspector.get_columns("message")})

    with Session(engine) as db_session:
        repository = SessionRepository(db_session)

        sessions = repository.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].active_branch_id == "session-1"

        branches = repository.list_branches("session-1")
        assert len(branches) == 1
        assert branches[0].id == "session-1"

        messages = repository.list_messages("session-1", branch_id="session-1")
        assert [message.id for message in messages] == ["message-1", "message-2"]
        assert [message.sequence for message in messages] == [1, 2]
        assert [message.turn_index for message in messages] == [1, 1]
        assert messages[0].parent_message_id is None
        assert messages[1].parent_message_id == "message-1"
        assert all(message.branch_id == "session-1" for message in messages)
        assert all(message.version_group_id == message.id for message in messages)
        assert all(message.completed_at is not None for message in messages)


def test_init_db_adds_generation_step_status_for_existing_sqlite_db(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy-generation-step.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE session (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    deleted_at DATETIME NULL,
                    active_branch_id TEXT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE message (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    attachments JSON NOT NULL,
                    created_at DATETIME NOT NULL,
                    branch_id TEXT NULL,
                    generation_id TEXT NULL,
                    status TEXT NOT NULL DEFAULT 'COMPLETED',
                    message_kind TEXT NOT NULL DEFAULT 'MESSAGE',
                    sequence INTEGER NOT NULL DEFAULT 0,
                    turn_index INTEGER NOT NULL DEFAULT 0,
                    metadata JSON NOT NULL DEFAULT '{}'
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE chat_generation (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    branch_id TEXT NOT NULL,
                    user_message_id TEXT NULL,
                    assistant_message_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    action TEXT NOT NULL DEFAULT 'REPLY',
                    metadata JSON NOT NULL DEFAULT '{}',
                    reasoning_trace JSON NOT NULL DEFAULT '[]',
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE generation_step (
                    id TEXT PRIMARY KEY,
                    generation_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    message_id TEXT NULL,
                    sequence INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    phase TEXT NULL,
                    state TEXT NULL,
                    label TEXT NULL,
                    safe_summary TEXT NULL,
                    delta_text TEXT NOT NULL DEFAULT '',
                    tool_name TEXT NULL,
                    tool_call_id TEXT NULL,
                    command TEXT NULL,
                    metadata JSON NOT NULL DEFAULT '{}',
                    started_at DATETIME NOT NULL,
                    ended_at DATETIME NULL
                )
                """
            )
        )

    monkeypatch.setattr(db_session_module, "engine", engine)

    db_session_module.init_db()

    inspector = inspect(engine)
    generation_step_columns = {
        column["name"] for column in inspector.get_columns("generation_step")
    }

    assert "status" in generation_step_columns
