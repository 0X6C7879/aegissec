from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.core.settings import Settings
from app.main import app
from app.services.model_api_settings import (
    ModelAPISettingsService,
    get_model_api_settings_service,
)
from tests.utils import api_data


def test_get_model_api_settings_returns_safe_response(
    client: TestClient,
    test_settings: Settings,
) -> None:
    test_settings.llm_api_base_url = "https://api.example.test/v1"
    test_settings.llm_api_key = "secret-token"
    test_settings.llm_default_model = "demo-model"

    response = client.get("/api/settings/model-api")

    assert response.status_code == 200
    assert api_data(response) == {
        "provider": "openai",
        "base_url": "https://api.example.test/v1",
        "model": "demo-model",
        "api_key_configured": True,
        "anthropic_base_url": None,
        "anthropic_model": None,
        "anthropic_api_key_configured": False,
    }
    assert "api_key" not in api_data(response)


def test_put_model_api_settings_persists_values_clears_cache_and_hides_secret(
    client: TestClient,
    test_settings: Settings,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local_path = tmp_path / ".env.local"
    env_local_path.write_text(
        "OTHER_SETTING=keep\n"
        "LLM_API_KEY=old-secret\n"
        "LLM_API_BASE_URL=https://old.example.test/v1\n"
        "LLM_DEFAULT_MODEL=old-model\n",
        encoding="utf-8",
    )

    test_settings.llm_api_base_url = "https://old.example.test/v1"
    test_settings.llm_api_key = "old-secret"
    test_settings.llm_default_model = "old-model"

    cache_clear_calls = 0

    def fake_cache_clear() -> None:
        nonlocal cache_clear_calls
        cache_clear_calls += 1

    monkeypatch.setattr(
        "app.services.model_api_settings.get_settings.cache_clear", fake_cache_clear
    )
    app.dependency_overrides[get_model_api_settings_service] = lambda: ModelAPISettingsService(
        test_settings,
        env_local_path=env_local_path,
    )

    try:
        response = client.put(
            "/api/settings/model-api",
            json={
                "base_url": "https://api.example.test/v1",
                "api_key": "new-secret-token",
                "model": "demo-model",
            },
        )

        assert response.status_code == 200
        assert api_data(response) == {
            "provider": "openai",
            "base_url": "https://api.example.test/v1",
            "model": "demo-model",
            "api_key_configured": True,
            "anthropic_base_url": None,
            "anthropic_model": None,
            "anthropic_api_key_configured": False,
        }
        assert "api_key" not in api_data(response)
        assert cache_clear_calls == 1
        assert env_local_path.read_text(encoding="utf-8") == (
            "OTHER_SETTING=keep\n"
            "LLM_API_KEY=new-secret-token\n"
            "LLM_API_BASE_URL=https://api.example.test/v1\n"
            "LLM_DEFAULT_MODEL=demo-model\n"
            "LLM_PROVIDER=openai\n"
        )

        test_settings.llm_api_base_url = "https://api.example.test/v1"
        test_settings.llm_api_key = "new-secret-token"
        test_settings.llm_default_model = "demo-model"

        clear_response = client.put(
            "/api/settings/model-api",
            json={
                "api_key": "",
            },
        )

        assert clear_response.status_code == 200
        assert api_data(clear_response) == {
            "provider": "openai",
            "base_url": "https://api.example.test/v1",
            "model": "demo-model",
            "api_key_configured": False,
            "anthropic_base_url": None,
            "anthropic_model": None,
            "anthropic_api_key_configured": False,
        }
        env_local_content = env_local_path.read_text(encoding="utf-8")
        assert "LLM_API_KEY=" not in env_local_content
        assert "OTHER_SETTING=keep" in env_local_content
        assert cache_clear_calls == 2
    finally:
        app.dependency_overrides.pop(get_model_api_settings_service, None)


def test_get_model_api_settings_returns_anthropic_when_configured(
    client: TestClient,
    test_settings: Settings,
) -> None:
    test_settings.llm_provider = "anthropic"
    test_settings.anthropic_api_key = "sk-ant-xxxxx"
    test_settings.anthropic_api_base_url = "https://anthropic-proxy.example.test/v1"
    test_settings.anthropic_model = "claude-3-5-sonnet-20241022"

    response = client.get("/api/settings/model-api")

    assert response.status_code == 200
    data = api_data(response)
    assert data["provider"] == "anthropic"
    assert data["anthropic_base_url"] == "https://anthropic-proxy.example.test/v1"
    assert data["anthropic_model"] == "claude-3-5-sonnet-20241022"
    assert data["anthropic_api_key_configured"] is True
    assert "anthropic_api_key" not in data


def test_put_model_api_settings_switches_to_anthropic_and_persists(
    client: TestClient,
    test_settings: Settings,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local_path = tmp_path / ".env.local"
    env_local_path.write_text(
        "LLM_PROVIDER=openai\n"
        "LLM_API_KEY=old-openai-key\n"
        "LLM_API_BASE_URL=https://api.openai.com/v1\n"
        "LLM_DEFAULT_MODEL=gpt-4\n",
        encoding="utf-8",
    )

    test_settings.llm_provider = "openai"
    test_settings.llm_api_key = "old-openai-key"
    test_settings.llm_api_base_url = "https://api.openai.com/v1"
    test_settings.llm_default_model = "gpt-4"

    cache_clear_calls = 0

    def fake_cache_clear() -> None:
        nonlocal cache_clear_calls
        cache_clear_calls += 1

    monkeypatch.setattr(
        "app.services.model_api_settings.get_settings.cache_clear", fake_cache_clear
    )
    app.dependency_overrides[get_model_api_settings_service] = lambda: ModelAPISettingsService(
        test_settings,
        env_local_path=env_local_path,
    )

    try:
        response = client.put(
            "/api/settings/model-api",
            json={
                "provider": "anthropic",
                "anthropic_api_key": "sk-ant-xxxxx",
                "anthropic_base_url": "https://anthropic-proxy.example.test/v1",
                "anthropic_model": "claude-3-5-sonnet-20241022",
            },
        )

        assert response.status_code == 200
        data = api_data(response)
        assert data["provider"] == "anthropic"
        assert data["anthropic_base_url"] == "https://anthropic-proxy.example.test/v1"
        assert data["anthropic_model"] == "claude-3-5-sonnet-20241022"
        assert data["anthropic_api_key_configured"] is True

        env_local_content = env_local_path.read_text(encoding="utf-8")
        assert "LLM_PROVIDER=anthropic" in env_local_content
        assert "ANTHROPIC_API_KEY=sk-ant-xxxxx" in env_local_content
        assert "ANTHROPIC_API_BASE_URL=https://anthropic-proxy.example.test/v1" in env_local_content
        assert "ANTHROPIC_MODEL=claude-3-5-sonnet-20241022" in env_local_content
        assert cache_clear_calls == 1

        test_settings.llm_provider = "anthropic"
        test_settings.anthropic_api_key = "sk-ant-xxxxx"
        test_settings.anthropic_api_base_url = "https://anthropic-proxy.example.test/v1"
        test_settings.anthropic_model = "claude-3-5-sonnet-20241022"

        clear_response = client.put(
            "/api/settings/model-api",
            json={
                "clear_anthropic_api_key": True,
            },
        )

        assert clear_response.status_code == 200
        clear_data = api_data(clear_response)
        assert clear_data["anthropic_api_key_configured"] is False

        env_local_content = env_local_path.read_text(encoding="utf-8")
        assert "ANTHROPIC_API_KEY=" not in env_local_content
        assert cache_clear_calls == 2
    finally:
        app.dependency_overrides.pop(get_model_api_settings_service, None)


def test_put_model_api_settings_updates_anthropic_model_only(
    client: TestClient,
    test_settings: Settings,
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    env_local_path = tmp_path / ".env.local"
    env_local_path.write_text(
        "LLM_PROVIDER=anthropic\n"
        "ANTHROPIC_API_KEY=sk-ant-xxxxx\n"
        "ANTHROPIC_API_BASE_URL=https://anthropic-proxy.example.test/v1\n"
        "ANTHROPIC_MODEL=claude-3-sonnet-20240229\n",
        encoding="utf-8",
    )

    test_settings.llm_provider = "anthropic"
    test_settings.anthropic_api_key = "sk-ant-xxxxx"
    test_settings.anthropic_api_base_url = "https://anthropic-proxy.example.test/v1"
    test_settings.anthropic_model = "claude-3-sonnet-20240229"

    cache_clear_calls = 0

    def fake_cache_clear() -> None:
        nonlocal cache_clear_calls
        cache_clear_calls += 1

    monkeypatch.setattr(
        "app.services.model_api_settings.get_settings.cache_clear", fake_cache_clear
    )
    app.dependency_overrides[get_model_api_settings_service] = lambda: ModelAPISettingsService(
        test_settings,
        env_local_path=env_local_path,
    )

    try:
        response = client.put(
            "/api/settings/model-api",
            json={
                "anthropic_model": "claude-3-5-sonnet-20241022",
            },
        )

        assert response.status_code == 200
        data = api_data(response)
        assert data["anthropic_base_url"] == "https://anthropic-proxy.example.test/v1"
        assert data["anthropic_model"] == "claude-3-5-sonnet-20241022"
        assert data["anthropic_api_key_configured"] is True

        env_local_content = env_local_path.read_text(encoding="utf-8")
        assert "ANTHROPIC_API_BASE_URL=https://anthropic-proxy.example.test/v1" in env_local_content
        assert "ANTHROPIC_MODEL=claude-3-5-sonnet-20241022" in env_local_content
        assert "ANTHROPIC_API_KEY=sk-ant-xxxxx" in env_local_content
        assert cache_clear_calls == 1
    finally:
        app.dependency_overrides.pop(get_model_api_settings_service, None)
