import base64

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from app.main import app
from tests.utils import api_data


def test_auth_status_reports_disabled_by_default(client: TestClient) -> None:
    response = client.get("/api/auth/status")

    assert response.status_code == 200
    assert api_data(response) == {"mode": "disabled", "token_required": False}


def test_token_auth_mode_protects_api_routes(client: TestClient) -> None:
    settings = app.state.settings
    original_mode = settings.api_auth_mode
    original_token = settings.api_auth_token

    settings.api_auth_mode = "token"
    settings.api_auth_token = "module-a-secret"

    try:
        unauthorized_response = client.get("/api/sessions")
        assert unauthorized_response.status_code == 401
        assert unauthorized_response.json()["detail"] == "Missing bearer token"

        health_response = client.get("/api/health")
        assert health_response.status_code == 200

        authorized_response = client.get(
            "/api/sessions",
            headers={"Authorization": "Bearer module-a-secret"},
        )
        assert authorized_response.status_code == 200
        assert api_data(authorized_response) == []
    finally:
        settings.api_auth_mode = original_mode
        settings.api_auth_token = original_token


def test_token_auth_mode_allows_cors_preflight(client: TestClient) -> None:
    settings = app.state.settings
    original_mode = settings.api_auth_mode
    original_token = settings.api_auth_token

    settings.api_auth_mode = "token"
    settings.api_auth_token = "module-a-secret"

    try:
        response = client.options(
            "/api/sessions/8c57b43a-8225-4d86-b620-667dddcc282f/graphs/attack",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    finally:
        settings.api_auth_mode = original_mode
        settings.api_auth_token = original_token


def test_basic_auth_mode_allows_cors_preflight(client: TestClient) -> None:
    settings = app.state.settings
    original_mode = settings.api_auth_mode
    original_username = settings.api_auth_username
    original_password = settings.api_auth_password

    settings.api_auth_mode = "basic"
    settings.api_auth_username = "operator"
    settings.api_auth_password = "module-a-secret"

    try:
        response = client.options(
            "/api/sessions/8c57b43a-8225-4d86-b620-667dddcc282f/graphs/attack",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    finally:
        settings.api_auth_mode = original_mode
        settings.api_auth_username = original_username
        settings.api_auth_password = original_password


def test_basic_auth_mode_supports_login_and_websocket(client: TestClient) -> None:
    settings = app.state.settings
    original_mode = settings.api_auth_mode
    original_username = settings.api_auth_username
    original_password = settings.api_auth_password

    settings.api_auth_mode = "basic"
    settings.api_auth_username = "operator"
    settings.api_auth_password = "module-a-secret"

    basic_token = base64.b64encode(b"operator:module-a-secret").decode("utf-8")
    basic_header = {"Authorization": f"Basic {basic_token}"}

    try:
        unauthorized_response = client.get("/api/sessions")
        assert unauthorized_response.status_code == 401
        assert unauthorized_response.json()["detail"] == "Missing basic credentials"

        failed_login_response = client.post(
            "/api/auth/login",
            json={"username": "operator", "password": "wrong"},
        )
        assert failed_login_response.status_code == 401
        assert failed_login_response.json()["detail"] == "Invalid username or password"

        login_response = client.post(
            "/api/auth/login",
            json={"username": "operator", "password": "module-a-secret"},
        )
        assert login_response.status_code == 200
        assert api_data(login_response) == {"mode": "basic", "authenticated": True}

        session_response = client.post(
            "/api/sessions",
            json={"title": "basic-auth-websocket"},
            headers=basic_header,
        )
        assert session_response.status_code == 201
        session_id = str(api_data(session_response)["id"])

        with pytest.raises(WebSocketDenialResponse) as exc_info:
            with client.websocket_connect(f"/api/sessions/{session_id}/events"):
                pass
        assert exc_info.value.status_code == 401
        assert "Missing basic credentials" in exc_info.value.text

        with client.websocket_connect(
            f"/api/sessions/{session_id}/events?auth_basic={basic_token}"
        ) as websocket:
            websocket.close()

        authorized_response = client.get("/api/sessions", headers=basic_header)
        assert authorized_response.status_code == 200
    finally:
        settings.api_auth_mode = original_mode
        settings.api_auth_username = original_username
        settings.api_auth_password = original_password
