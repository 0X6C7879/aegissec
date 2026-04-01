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
        "runtime_artifact",
        "runtime_execution_run",
        "run_log",
    }.issubset(table_names)
    session_columns = {column["name"] for column in inspector.get_columns("session")}
    assert {"project_id", "goal", "scenario_type", "current_phase", "runtime_policy_json"}.issubset(
        session_columns
    )
