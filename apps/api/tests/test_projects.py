from fastapi.testclient import TestClient

from tests.utils import api_data


def test_project_lifecycle_and_session_linking(client: TestClient) -> None:
    create_response = client.post(
        "/api/projects",
        json={"name": "Module A", "description": "Backend delivery slice"},
    )

    assert create_response.status_code == 201
    created_project = api_data(create_response)
    project_id = created_project["id"]
    assert created_project["name"] == "Module A"
    assert created_project["description"] == "Backend delivery slice"

    list_response = client.get("/api/projects")

    assert list_response.status_code == 200
    assert [project["id"] for project in api_data(list_response)] == [project_id]

    session_response = client.post(
        "/api/sessions",
        json={
            "title": "Project linked session",
            "project_id": project_id,
            "goal": "Exercise project linkage",
            "scenario_type": "api",
            "current_phase": "planning",
            "runtime_policy_json": {"network": "isolated"},
        },
    )

    assert session_response.status_code == 201
    assert api_data(session_response)["project_id"] == project_id

    detail_response = client.get(f"/api/projects/{project_id}")

    assert detail_response.status_code == 200
    detail_payload = api_data(detail_response)
    assert detail_payload["id"] == project_id
    assert [session["project_id"] for session in detail_payload["sessions"]] == [project_id]

    update_response = client.patch(
        f"/api/projects/{project_id}",
        json={"name": "Module A Updated", "description": "Verified project routes"},
    )

    assert update_response.status_code == 200
    update_payload = api_data(update_response)
    assert update_payload["name"] == "Module A Updated"
    assert update_payload["description"] == "Verified project routes"

    delete_response = client.delete(f"/api/projects/{project_id}")

    assert delete_response.status_code == 200
    assert api_data(client.get("/api/projects")) == []

    restore_response = client.post(f"/api/projects/{project_id}/restore")

    assert restore_response.status_code == 200
    assert api_data(restore_response)["deleted_at"] is None


def test_project_settings_are_separate_from_user_model_api_settings(client: TestClient) -> None:
    create_response = client.post(
        "/api/projects",
        json={"name": "Scoped Settings Project", "description": "project scoped defaults"},
    )
    project_id = api_data(create_response)["id"]

    empty_settings_response = client.get(f"/api/projects/{project_id}/settings")
    assert empty_settings_response.status_code == 200
    empty_payload = api_data(empty_settings_response)
    assert empty_payload["project_id"] == project_id
    assert empty_payload["default_workflow_template"] is None
    assert empty_payload["default_runtime_profile_name"] is None
    assert empty_payload["runtime_defaults"] == {}

    update_settings_response = client.patch(
        f"/api/projects/{project_id}/settings",
        json={
            "default_workflow_template": "authorized-assessment",
            "default_runtime_profile_name": "strict-lab",
            "default_queue_backend": "redis",
            "runtime_defaults": {"allow_network": False, "allow_write": True},
            "notes": "Project scoped execution defaults.",
        },
    )

    assert update_settings_response.status_code == 200
    updated_settings = api_data(update_settings_response)
    assert updated_settings["default_workflow_template"] == "authorized-assessment"
    assert updated_settings["default_runtime_profile_name"] == "strict-lab"
    assert updated_settings["default_queue_backend"] == "redis"
    assert updated_settings["runtime_defaults"] == {"allow_network": False, "allow_write": True}
    assert updated_settings["notes"] == "Project scoped execution defaults."

    project_detail_response = client.get(f"/api/projects/{project_id}")
    assert project_detail_response.status_code == 200

    user_settings_response = client.get("/api/settings/model-api")
    assert user_settings_response.status_code == 200
    assert set(api_data(user_settings_response).keys()) == {
        "provider",
        "base_url",
        "model",
        "api_key_configured",
        "anthropic_base_url",
        "anthropic_model",
        "anthropic_api_key_configured",
    }
