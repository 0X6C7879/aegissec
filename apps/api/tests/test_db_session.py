from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlmodel import create_engine

from app.db import session as session_module


def test_init_db_repairs_legacy_phase6_sqlite_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_path = tmp_path / "legacy-phase6.db"
    sqlite_connection = sqlite3.connect(database_path)
    try:
        sqlite_connection.executescript(
            """
            CREATE TABLE workflow_run (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                template_name TEXT NOT NULL,
                status TEXT NOT NULL,
                current_stage TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE task_node (
                id TEXT PRIMARY KEY,
                workflow_run_id TEXT NOT NULL,
                name TEXT NOT NULL,
                node_type TEXT NOT NULL,
                status TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                parent_id TEXT,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE graph_node (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                graph_type TEXT NOT NULL,
                node_type TEXT NOT NULL,
                label TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE graph_edge (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                graph_type TEXT NOT NULL,
                source_node_id TEXT NOT NULL,
                target_node_id TEXT NOT NULL,
                relation TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        sqlite_connection.commit()
    finally:
        sqlite_connection.close()

    test_engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    monkeypatch.setattr(session_module, "engine", test_engine)

    session_module.init_db()

    inspector = inspect(test_engine)
    workflow_columns = {column["name"] for column in inspector.get_columns("workflow_run")}
    graph_node_columns = {column["name"] for column in inspector.get_columns("graph_node")}
    graph_edge_columns = {column["name"] for column in inspector.get_columns("graph_edge")}

    assert "state" in workflow_columns
    assert "workflow_run_id" in graph_node_columns
    assert "workflow_run_id" in graph_edge_columns
