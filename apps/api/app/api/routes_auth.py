from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.api import ok_response
from app.core.settings import Settings, get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class AuthStatusRead(BaseModel):
    mode: str
    token_required: bool


@router.get("/status")
async def get_auth_status(settings: Settings = Depends(get_settings)) -> object:
    return ok_response(
        AuthStatusRead(
            mode=settings.api_auth_mode,
            token_required=settings.api_auth_mode == "token",
        ).model_dump(mode="json")
    )
