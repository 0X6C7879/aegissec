from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.services.model_api_settings import (
    ModelAPISettingsService,
    ModelAPISettingsUpdate,
    get_model_api_settings_service,
)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ModelAPISettingsResponse(BaseModel):
    base_url: str | None
    model: str | None
    api_key_configured: bool


class UpdateModelAPISettingsRequest(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None


@router.get("/model-api", response_model=ModelAPISettingsResponse)
def get_model_api_settings(
    settings_service: ModelAPISettingsService = Depends(get_model_api_settings_service),
) -> ModelAPISettingsResponse:
    state = settings_service.get_settings_state()
    return ModelAPISettingsResponse(
        base_url=state.base_url,
        model=state.model,
        api_key_configured=state.api_key_configured,
    )


@router.put("/model-api", response_model=ModelAPISettingsResponse)
def update_model_api_settings(
    request: UpdateModelAPISettingsRequest,
    settings_service: ModelAPISettingsService = Depends(get_model_api_settings_service),
) -> ModelAPISettingsResponse:
    state = settings_service.update_settings(
        ModelAPISettingsUpdate(
            base_url=request.base_url,
            api_key=request.api_key,
            model=request.model,
        ),
        fields_to_update=set(request.model_fields_set),
    )
    return ModelAPISettingsResponse(
        base_url=state.base_url,
        model=state.model,
        api_key_configured=state.api_key_configured,
    )
