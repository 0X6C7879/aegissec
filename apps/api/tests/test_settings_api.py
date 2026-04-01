from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.core.settings import Settings, get_settings
from app.main import app
from tests.utils import api_data


@pytest.fixture
def isolated_settings_env(tmp_path: Path, monkeypatch: MonkeyPatch) -> Generator[Path, None, None]:
    env_file = tmp_path / ".env"
    env_local_file = tmp_path / ".env.local"
    original_env_file = Settings.model_config.get("env_file")

    monkeypatch.setitem(
        Settings.model_config,
        "env_file",
        (str(env_file), str(env_local_file)),
    )
    monkeypatch.setattr("app.services.model_api_settings.REPO_ROOT", tmp_path)
    get_settings.cache_clear()

    yield env_local_file

    get_settings.cache_clear()
    Settings.model_config["env_file"] = original_env_file


def test_get_model_api_settings_masks_api_key(isolated_settings_env: Path) -> None:
    isolated_settings_env.write_text(
        "\n".join(
            [
                'LLM_API_BASE_URL="https://example.com/v1"',
                'LLM_DEFAULT_MODEL="gpt-5"',
                'LLM_API_KEY="stored-secret"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()

    with TestClient(app) as client:
        response = client.get("/api/settings/model-api")

    assert response.status_code == 200
    assert api_data(response) == {
        "provider": "openai",
        "base_url": "https://example.com/v1",
        "model": "gpt-5",
        "api_key_configured": True,
        "anthropic_base_url": None,
        "anthropic_model": None,
        "anthropic_api_key_configured": False,
    }


def test_put_model_api_settings_round_trips_and_clears_cache(isolated_settings_env: Path) -> None:
    assert get_settings().llm_default_model is None

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/model-api",
            json={
                "base_url": "https://api.example.com/v1",
                "model": "model-alpha",
                "api_key": "new-secret",
                "clear_api_key": False,
            },
        )

        follow_up_response = client.get("/api/settings/model-api")

    assert response.status_code == 200
    assert api_data(response) == {
        "provider": "openai",
        "base_url": "https://api.example.com/v1",
        "model": "model-alpha",
        "api_key_configured": True,
        "anthropic_base_url": None,
        "anthropic_model": None,
        "anthropic_api_key_configured": False,
    }
    assert follow_up_response.status_code == 200
    assert api_data(follow_up_response) == api_data(response)
    assert get_settings().llm_api_base_url == "https://api.example.com/v1"
    assert get_settings().llm_default_model == "model-alpha"
    assert get_settings().llm_api_key == "new-secret"


def test_put_model_api_settings_preserves_existing_key_when_not_replaced(
    isolated_settings_env: Path,
) -> None:
    isolated_settings_env.write_text(
        "\n".join(
            [
                "# existing settings",
                'LLM_API_BASE_URL="https://old.example.com/v1"',
                'LLM_DEFAULT_MODEL="model-old"',
                'LLM_API_KEY="preserved-secret"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/model-api",
            json={
                "base_url": "https://new.example.com/v1",
                "model": None,
                "api_key": None,
                "clear_api_key": False,
            },
        )

    env_local_text = isolated_settings_env.read_text(encoding="utf-8")

    assert response.status_code == 200
    assert api_data(response) == {
        "provider": "openai",
        "base_url": "https://new.example.com/v1",
        "model": None,
        "api_key_configured": True,
        "anthropic_base_url": None,
        "anthropic_model": None,
        "anthropic_api_key_configured": False,
    }
    assert env_local_text.startswith("# existing settings\n")
    assert env_local_text.count("LLM_API_KEY=") == 1
    assert "LLM_DEFAULT_MODEL=" not in env_local_text
    assert get_settings().llm_api_key == "preserved-secret"


def test_put_model_api_settings_can_clear_api_key_and_base_url(isolated_settings_env: Path) -> None:
    isolated_settings_env.write_text(
        "\n".join(
            [
                'LLM_API_BASE_URL="https://clear.example.com/v1"',
                'LLM_DEFAULT_MODEL="model-before-clear"',
                'LLM_API_KEY="clear-me"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()

    with TestClient(app) as client:
        response = client.put(
            "/api/settings/model-api",
            json={
                "base_url": None,
                "model": "model-after-clear",
                "api_key": None,
                "clear_api_key": True,
            },
        )

    env_local_text = isolated_settings_env.read_text(encoding="utf-8")

    assert response.status_code == 200
    assert api_data(response) == {
        "provider": "openai",
        "base_url": None,
        "model": "model-after-clear",
        "api_key_configured": False,
        "anthropic_base_url": None,
        "anthropic_model": None,
        "anthropic_api_key_configured": False,
    }
    assert "LLM_API_BASE_URL=" not in env_local_text
    assert "LLM_API_KEY=" not in env_local_text
    assert 'LLM_DEFAULT_MODEL="model-after-clear"' in env_local_text
    assert get_settings().llm_api_key is None
