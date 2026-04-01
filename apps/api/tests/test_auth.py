from fastapi.testclient import TestClient

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
