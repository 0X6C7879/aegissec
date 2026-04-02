from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect

from alembic import command
from alembic.config import Config


def test_alembic_upgrade_head_creates_module_a_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "alembic.db"
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")

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
