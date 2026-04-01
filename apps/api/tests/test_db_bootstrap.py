from pathlib import Path

from pytest import MonkeyPatch
from sqlalchemy import inspect, text
from sqlmodel import create_engine

from app.db import session as db_session_module


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
