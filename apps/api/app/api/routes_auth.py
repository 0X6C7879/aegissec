from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.core.api import ok_response
from app.core.auth import validate_basic_credentials
from app.core.settings import Settings, get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])


class AuthStatusRead(BaseModel):
    mode: str
    token_required: bool


class AuthLoginRequest(BaseModel):
    username: str
    password: str


class AuthLoginRead(BaseModel):
    mode: str
    authenticated: bool


@router.get("/status")
async def get_auth_status(settings: Settings = Depends(get_settings)) -> object:
    return ok_response(
        AuthStatusRead(
            mode=settings.api_auth_mode,
            token_required=settings.api_auth_mode == "token",
        ).model_dump(mode="json")
    )


@router.post("/login", response_model=AuthLoginRead)
async def login_with_username_password(
    payload: AuthLoginRequest,
    settings: Settings = Depends(get_settings),
) -> object:
    if settings.api_auth_mode != "basic":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username/password login is not enabled",
        )

    authorized, reason = validate_basic_credentials(payload.username, payload.password, settings)
    if not authorized:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=reason or "Invalid username or password",
        )

    return ok_response(
        AuthLoginRead(
            mode=settings.api_auth_mode,
            authenticated=True,
        ).model_dump(mode="json")
    )
