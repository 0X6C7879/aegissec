from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect

from alembic import command  # pyright: ignore[reportMissingImports, reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]


def _build_alembic_config(database_path: Path) -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    return config


def test_alembic_upgrade_head_creates_module_a_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "alembic.db"
    config = _build_alembic_config(database_path)

    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{database_path.as_posix()}"))
    table_names = set(inspector.get_table_names())
    assert {
        "project",
        "project_settings",
        "session",
        "conversation_branch",
        "chat_generation",
        "message",
        "runtime_artifact",
        "runtime_execution_run",
        "runtime_terminal_jobs",
        "runtime_terminal_sessions",
        "run_log",
        "session_event_log",
    }.issubset(table_names)
    session_columns = {column["name"] for column in inspector.get_columns("session")}
    assert {
        "project_id",
        "goal",
        "scenario_type",
        "current_phase",
        "runtime_policy_json",
        "active_branch_id",
    }.issubset(session_columns)
    message_columns = {column["name"] for column in inspector.get_columns("message")}
    assert {
        "parent_message_id",
        "branch_id",
        "generation_id",
        "status",
        "message_kind",
        "sequence",
        "turn_index",
        "edited_from_message_id",
        "version_group_id",
        "metadata",
        "error_message",
        "completed_at",
    }.issubset(message_columns)
    generation_columns = {column["name"] for column in inspector.get_columns("chat_generation")}
    assert {
        "action",
        "target_message_id",
        "reasoning_summary",
        "reasoning_trace",
        "cancel_requested_at",
        "worker_id",
        "lease_claimed_at",
        "lease_expires_at",
        "attempt_count",
    }.issubset(generation_columns)
    session_event_columns = {
        column["name"] for column in inspector.get_columns("session_event_log")
    }
    assert {"cursor", "session_id", "event_type", "timestamp", "payload"}.issubset(
        session_event_columns
    )
    terminal_session_columns = {
        column["name"] for column in inspector.get_columns("runtime_terminal_sessions")
    }
    assert {
        "session_id",
        "title",
        "status",
        "shell",
        "cwd",
        "metadata",
        "closed_at",
    }.issubset(terminal_session_columns)
    terminal_job_columns = {
        column["name"] for column in inspector.get_columns("runtime_terminal_jobs")
    }
    assert {
        "terminal_session_id",
        "session_id",
        "status",
        "command",
        "exit_code",
        "started_at",
        "ended_at",
        "metadata",
    }.issubset(terminal_job_columns)


def test_alembic_latest_revision_upgrade_and_downgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "alembic-indexes.db"
    config = _build_alembic_config(database_path)
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")

    command.upgrade(config, "head")

    inspector = inspect(engine)
    message_index_names = {
        index["name"] for index in inspector.get_indexes("message")
    }
    runtime_run_index_names = {
        index["name"] for index in inspector.get_indexes("runtime_execution_run")
    }
    assert "ix_message_session_branch_status_sequence" in message_index_names
    assert "ix_runtime_execution_run_started_id" in runtime_run_index_names

    command.downgrade(config, "-1")

    downgraded_inspector = inspect(engine)
    downgraded_message_index_names = {
        index["name"] for index in downgraded_inspector.get_indexes("message")
    }
    downgraded_runtime_run_index_names = {
        index["name"] for index in downgraded_inspector.get_indexes("runtime_execution_run")
    }
    assert "ix_message_session_branch_status_sequence" not in downgraded_message_index_names
    assert "ix_runtime_execution_run_started_id" not in downgraded_runtime_run_index_names
