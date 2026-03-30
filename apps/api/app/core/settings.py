from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]
API_ROOT = Path(__file__).resolve().parents[2]


def _default_database_url() -> str:
    database_path = (API_ROOT / "data" / "aegissec.db").resolve()
    return f"sqlite:///{database_path.as_posix()}"


def _default_mcp_import_paths() -> list[str]:
    return []


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"), str(REPO_ROOT / ".env.local")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = Field(default="aegissec", alias="AEGISSEC_APP_NAME")
    app_version: str = "0.1.0"
    api_host: str = Field(default="127.0.0.1", alias="AEGISSEC_API_HOST")
    api_port: int = Field(default=8000, alias="AEGISSEC_API_PORT")
    frontend_origin: str = Field(
        default="http://127.0.0.1:5173",
        alias="AEGISSEC_FRONTEND_ORIGIN",
    )
    kali_image: str = Field(default="aegissec-kali:latest", alias="AEGISSEC_KALI_IMAGE")
    runtime_container_name: str = Field(
        default="aegissec-kali-runtime",
        alias="AEGISSEC_RUNTIME_CONTAINER_NAME",
    )
    runtime_workspace_dir: str = Field(
        default=str((API_ROOT / "data" / "runtime-workspace").resolve()),
        alias="AEGISSEC_RUNTIME_WORKSPACE_DIR",
    )
    runtime_workspace_container_path: str = Field(
        default="/workspace",
        alias="AEGISSEC_RUNTIME_WORKSPACE_CONTAINER_PATH",
    )
    runtime_default_timeout_seconds: int = Field(
        default=300,
        alias="AEGISSEC_RUNTIME_DEFAULT_TIMEOUT_SECONDS",
    )
    runtime_recent_runs_limit: int = Field(
        default=10,
        alias="AEGISSEC_RUNTIME_RECENT_RUNS_LIMIT",
    )
    runtime_recent_artifacts_limit: int = Field(
        default=20,
        alias="AEGISSEC_RUNTIME_RECENT_ARTIFACTS_LIMIT",
    )
    mcp_import_paths: list[str] = Field(
        default_factory=_default_mcp_import_paths,
        alias="AEGISSEC_MCP_IMPORT_PATHS",
    )
    database_url: str = Field(default_factory=_default_database_url, alias="AEGISSEC_DATABASE_URL")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")
    llm_api_base_url: str | None = Field(default=None, alias="LLM_API_BASE_URL")
    llm_default_model: str | None = Field(default=None, alias="LLM_DEFAULT_MODEL")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
