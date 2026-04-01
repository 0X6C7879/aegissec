from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends

from app.core.settings import REPO_ROOT, Settings, get_settings

MODEL_API_ENV_KEYS = {
    "base_url": "LLM_API_BASE_URL",
    "api_key": "LLM_API_KEY",
    "model": "LLM_DEFAULT_MODEL",
}

ANTHROPIC_ENV_KEYS = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "anthropic_base_url": "ANTHROPIC_API_BASE_URL",
    "anthropic_model": "ANTHROPIC_MODEL",
}

LLM_PROVIDER_ENV_KEY = "LLM_PROVIDER"

_MANAGED_ENV_KEY_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?P<key>LLM_API_BASE_URL|LLM_API_KEY|LLM_DEFAULT_MODEL|LLM_PROVIDER|ANTHROPIC_API_KEY|ANTHROPIC_API_BASE_URL|ANTHROPIC_MODEL)\s*=.*$"
)


@dataclass(slots=True)
class ModelAPISettingsState:
    provider: str
    base_url: str | None
    model: str | None
    api_key_configured: bool
    anthropic_base_url: str | None
    anthropic_model: str | None
    anthropic_api_key_configured: bool


@dataclass(slots=True)
class ModelAPISettingsUpdate:
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    anthropic_model: str | None = None


class ModelAPISettingsService:
    def __init__(self, settings: Settings, env_local_path: Path | None = None) -> None:
        self._settings = settings
        self._env_local_path = env_local_path or REPO_ROOT / ".env.local"

    def get_settings_state(self) -> ModelAPISettingsState:
        return self._build_state(
            provider=self._settings.llm_provider,
            base_url=self._settings.llm_api_base_url,
            api_key=self._settings.llm_api_key,
            model=self._settings.llm_default_model,
            anthropic_api_key=self._settings.anthropic_api_key,
            anthropic_base_url=self._settings.anthropic_api_base_url,
            anthropic_model=self._settings.anthropic_model,
        )

    def update_settings(
        self,
        update: ModelAPISettingsUpdate,
        fields_to_update: set[str],
    ) -> ModelAPISettingsState:
        current_values = {
            "provider": self._settings.llm_provider,
            "base_url": self._settings.llm_api_base_url,
            "api_key": self._settings.llm_api_key,
            "model": self._settings.llm_default_model,
            "anthropic_api_key": self._settings.anthropic_api_key,
            "anthropic_base_url": self._settings.anthropic_api_base_url,
            "anthropic_model": self._settings.anthropic_model,
        }

        for field_name in fields_to_update:
            current_values[field_name] = self._normalize_value(getattr(update, field_name))

        provider_value = current_values["provider"] or "openai"
        current_values["provider"] = provider_value

        self._persist_values(current_values)
        get_settings.cache_clear()
        return self._build_state(
            provider=provider_value,
            base_url=current_values["base_url"],
            api_key=current_values["api_key"],
            model=current_values["model"],
            anthropic_api_key=current_values["anthropic_api_key"],
            anthropic_base_url=current_values["anthropic_base_url"],
            anthropic_model=current_values["anthropic_model"],
        )

    def _persist_values(self, values: dict[str, str | None]) -> None:
        existing_lines = self._read_env_lines()
        rendered_lines: list[str] = []
        seen_keys: set[str] = set()

        for line in existing_lines:
            managed_key = self._extract_managed_env_key(line)
            if managed_key is None:
                rendered_lines.append(line)
                continue

            if managed_key in seen_keys:
                continue

            seen_keys.add(managed_key)

            if managed_key == LLM_PROVIDER_ENV_KEY:
                provider_value = values.get("provider")
                if provider_value is None:
                    continue
                if self._line_uses_quoted_value(line):
                    rendered_lines.append(f"{LLM_PROVIDER_ENV_KEY}={json.dumps(provider_value)}")
                else:
                    rendered_lines.append(
                        f"{LLM_PROVIDER_ENV_KEY}={self._serialize_env_value(provider_value)}"
                    )
            elif managed_key in MODEL_API_ENV_KEYS.values():
                field_name = self._field_name_for_env_key(managed_key)
                field_value = values[field_name]
                if field_value is None:
                    continue
                if self._line_uses_quoted_value(line):
                    rendered_lines.append(f"{managed_key}={json.dumps(field_value)}")
                else:
                    rendered_lines.append(f"{managed_key}={self._serialize_env_value(field_value)}")
            elif managed_key in ANTHROPIC_ENV_KEYS.values():
                field_name = self._field_name_for_anthropic_env_key(managed_key)
                field_value = values.get(field_name)
                if field_value is None:
                    continue
                if self._line_uses_quoted_value(line):
                    rendered_lines.append(f"{managed_key}={json.dumps(field_value)}")
                else:
                    rendered_lines.append(f"{managed_key}={self._serialize_env_value(field_value)}")

        if LLM_PROVIDER_ENV_KEY not in seen_keys:
            provider_value = values.get("provider")
            if provider_value:
                rendered_lines.append(
                    f"{LLM_PROVIDER_ENV_KEY}={self._serialize_env_value(provider_value)}"
                )
                seen_keys.add(LLM_PROVIDER_ENV_KEY)

        for field_name, env_key in MODEL_API_ENV_KEYS.items():
            if env_key in seen_keys:
                continue
            field_value = values[field_name]
            if field_value is None:
                continue
            rendered_lines.append(f"{env_key}={self._serialize_env_value(field_value)}")

        for field_name, env_key in ANTHROPIC_ENV_KEYS.items():
            if env_key in seen_keys:
                continue
            field_value = values.get(field_name)
            if field_value is None:
                continue
            rendered_lines.append(f"{env_key}={self._serialize_env_value(field_value)}")

        content = "\n".join(rendered_lines)
        if rendered_lines:
            content += "\n"

        self._env_local_path.parent.mkdir(parents=True, exist_ok=True)
        self._env_local_path.write_text(content, encoding="utf-8")

    def _read_env_lines(self) -> list[str]:
        if not self._env_local_path.exists():
            return []
        return self._env_local_path.read_text(encoding="utf-8").splitlines()

    @staticmethod
    def _normalize_value(value: str | None) -> str | None:
        if value is None:
            return None

        normalized = value.strip()
        if not normalized:
            return None

        return normalized

    @staticmethod
    def _extract_managed_env_key(line: str) -> str | None:
        match = _MANAGED_ENV_KEY_PATTERN.match(line)
        if match is None:
            return None
        return str(match.group("key"))

    @staticmethod
    def _field_name_for_env_key(env_key: str) -> str:
        for field_name, configured_env_key in MODEL_API_ENV_KEYS.items():
            if configured_env_key == env_key:
                return field_name
        raise ValueError(f"Unsupported model API env key: {env_key}")

    @staticmethod
    def _field_name_for_anthropic_env_key(env_key: str) -> str:
        for field_name, configured_env_key in ANTHROPIC_ENV_KEYS.items():
            if configured_env_key == env_key:
                return field_name
        raise ValueError(f"Unsupported Anthropic env key: {env_key}")

    @staticmethod
    def _serialize_env_value(value: str) -> str:
        if any(character.isspace() for character in value) or any(
            character in value for character in ('"', "'", "#")
        ):
            return json.dumps(value)
        return value

    @staticmethod
    def _line_uses_quoted_value(line: str) -> bool:
        if "=" not in line:
            return False
        raw_value = line.split("=", 1)[1].lstrip()
        return raw_value.startswith('"') or raw_value.startswith("'")

    @staticmethod
    def _build_state(
        *,
        provider: str,
        base_url: str | None,
        api_key: str | None,
        model: str | None,
        anthropic_api_key: str | None,
        anthropic_base_url: str | None,
        anthropic_model: str | None,
    ) -> ModelAPISettingsState:
        return ModelAPISettingsState(
            provider=provider,
            base_url=base_url,
            model=model,
            api_key_configured=bool(api_key),
            anthropic_base_url=anthropic_base_url,
            anthropic_model=anthropic_model,
            anthropic_api_key_configured=bool(anthropic_api_key),
        )


def get_model_api_settings_service(
    settings: Settings = Depends(get_settings),
) -> ModelAPISettingsService:
    return ModelAPISettingsService(settings)
