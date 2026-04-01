from __future__ import annotations

from fastapi import Request

from app.core.settings import Settings

AUTH_EXEMPT_PATHS = {
    "/api/health",
    "/api/runtime/health",
    "/api/auth/status",
}


def is_api_request(path: str) -> bool:
    return path.startswith("/api")


def is_auth_exempt_path(path: str) -> bool:
    return path in AUTH_EXEMPT_PATHS


def is_request_authorized(request: Request, settings: Settings) -> tuple[bool, str | None]:
    if settings.api_auth_mode == "disabled":
        return True, None

    if settings.api_auth_mode == "local":
        client_host = request.client.host if request.client is not None else None
        if client_host in {"127.0.0.1", "localhost", "::1"}:
            return True, None
        return False, "Local mode only allows loopback clients"

    expected_token = settings.api_auth_token
    if expected_token is None or not expected_token.strip():
        return False, "API token auth is enabled but no token is configured"

    authorization_header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not authorization_header.startswith(prefix):
        return False, "Missing bearer token"

    provided_token = authorization_header.removeprefix(prefix).strip()
    if provided_token != expected_token:
        return False, "Invalid bearer token"

    return True, None
