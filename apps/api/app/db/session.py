from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.core.settings import get_settings


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


def init_db() -> None:
    if _is_sqlite_engine() and _phase6_schema_requires_rebuild():
        _rebuild_phase6_sqlite_tables()
    SQLModel.metadata.create_all(engine)


def get_db_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
