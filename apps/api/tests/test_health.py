from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_returns_scaffold_status() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "name": "aegissec",
        "status": "ok",
        "version": "0.1.0",
    }


def test_health_endpoint_allows_local_vite_fallback_origin() -> None:
    client = TestClient(app)

    response = client.get(
        "/health",
        headers={"Origin": "http://127.0.0.1:5174"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5174"
