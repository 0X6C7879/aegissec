from fastapi.testclient import TestClient

from app.main import app
from tests.utils import api_data


def test_health_endpoint_returns_scaffold_status() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert api_data(response) == {
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


def test_canonical_api_health_endpoint_matches_legacy_alias() -> None:
    client = TestClient(app)

    legacy_response = client.get("/health")
    canonical_response = client.get("/api/health")

    assert canonical_response.status_code == 200
    canonical_payload = canonical_response.json()
    assert canonical_payload["meta"]["request_id"]
    assert canonical_payload["data"] == api_data(legacy_response)


def test_openapi_metadata_exposes_module_a_p2_tags() -> None:
    client = TestClient(app)

    response = client.get("/openapi.json")

    assert response.status_code == 200
    payload = response.json()
    tags_by_name = {tag["name"]: tag for tag in payload["tags"]}
    assert payload["info"]["summary"] == "Local-first defensive security workbench API"
    assert "project-scoped defaults" in tags_by_name["projects"]["description"]
    assert "Workflow templates" in tags_by_name["workflows"]["description"]
