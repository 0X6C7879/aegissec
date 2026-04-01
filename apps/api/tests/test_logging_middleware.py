from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.core import logging_middleware
from app.db.repositories import RunLogRepository
from app.main import app
from tests.utils import api_data


def test_request_logging_reuses_request_scoped_db_session(
    client: TestClient,
    monkeypatch: MonkeyPatch,
) -> None:
    def fail_if_middleware_opens_independent_session(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("middleware should not open an independent database session")

    monkeypatch.setattr(
        logging_middleware,
        "DBSessionLocal",
        fail_if_middleware_opens_independent_session,
        raising=False,
    )

    create_response = client.post("/api/sessions", json={"title": "Logged Session"})
    assert create_response.status_code == 201
    session_id = api_data(create_response)["id"]

    detail_response = client.get(f"/api/sessions/{session_id}")
    assert detail_response.status_code == 200

    database_engine = app.state.database_engine
    assert isinstance(database_engine, Engine)
    with Session(database_engine) as db_session:
        logs = RunLogRepository(db_session).list_logs(session_id=session_id, limit=20)

    assert any(
        log.event_type == "request.completed" and log.message == f"GET /api/sessions/{session_id}"
        for log in logs
    )
