from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.api import ok_response
from app.services.model_api_settings import (
    ModelAPISettingsService,
    ModelAPISettingsUpdate,
    get_model_api_settings_service,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ModelAPISettingsResponse(BaseModel):
    provider: str
    # OpenAI fields
    base_url: str | None
    model: str | None
    api_key_configured: bool
    # Anthropic fields
    anthropic_base_url: str | None
    anthropic_model: str | None
    anthropic_api_key_configured: bool


class UpdateModelAPISettingsRequest(BaseModel):
    # Provider selection
    provider: str | None = None
    # OpenAI fields
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    clear_api_key: bool = False
    # Anthropic fields
    anthropic_api_key: str | None = None
    anthropic_base_url: str | None = None
    anthropic_model: str | None = None
    clear_anthropic_api_key: bool = False


@router.get(
    "/model-api",
    response_model=ModelAPISettingsResponse,
    summary="Get user model API settings",
    description="Return user-scoped model API defaults backed by local environment files.",
)
def get_model_api_settings(
    settings_service: ModelAPISettingsService = Depends(get_model_api_settings_service),
) -> object:
    state = settings_service.get_settings_state()
    return ok_response(
        ModelAPISettingsResponse(
            provider=state.provider,
            base_url=state.base_url,
            model=state.model,
            api_key_configured=state.api_key_configured,
            anthropic_base_url=state.anthropic_base_url,
            anthropic_model=state.anthropic_model,
            anthropic_api_key_configured=state.anthropic_api_key_configured,
        ).model_dump(mode="json")
    )


@router.put(
    "/model-api",
    response_model=ModelAPISettingsResponse,
    summary="Update user model API settings",
    description=(
        "Persist user-scoped LLM defaults in local env files without exposing "
        "the secret value in responses."
    ),
)
def update_model_api_settings(
    request: UpdateModelAPISettingsRequest,
    settings_service: ModelAPISettingsService = Depends(get_model_api_settings_service),
) -> object:
    fields_to_update = {
        field_name
        for field_name in request.model_fields_set
        if field_name not in ("clear_api_key", "clear_anthropic_api_key")
    }
    if "api_key" in fields_to_update and request.api_key is None and not request.clear_api_key:
        fields_to_update.discard("api_key")
    if request.clear_api_key:
        fields_to_update.add("api_key")

    if (
        "anthropic_api_key" in fields_to_update
        and request.anthropic_api_key is None
        and not request.clear_anthropic_api_key
    ):
        fields_to_update.discard("anthropic_api_key")
    if request.clear_anthropic_api_key:
        fields_to_update.add("anthropic_api_key")

    state = settings_service.update_settings(
        ModelAPISettingsUpdate(
            provider=request.provider,
            base_url=request.base_url,
            api_key=request.api_key,
            model=request.model,
            anthropic_api_key=request.anthropic_api_key,
            anthropic_base_url=request.anthropic_base_url,
            anthropic_model=request.anthropic_model,
        ),
        fields_to_update=fields_to_update,
    )
    return ok_response(
        ModelAPISettingsResponse(
            provider=state.provider,
            base_url=state.base_url,
            model=state.model,
            api_key_configured=state.api_key_configured,
            anthropic_base_url=state.anthropic_base_url,
            anthropic_model=state.anthropic_model,
            anthropic_api_key_configured=state.anthropic_api_key_configured,
        ).model_dump(mode="json")
    )
