from __future__ import annotations

import base64
import binascii
import hmac
from collections.abc import Mapping

from fastapi import Request, WebSocket

from app.core.settings import Settings

AUTH_EXEMPT_PATHS = {
    "/api/health",
    "/api/runtime/health",
    "/api/auth/status",
    "/api/auth/login",
}


def is_api_request(path: str) -> bool:
    return path.startswith("/api")


def is_auth_exempt_path(path: str) -> bool:
    return path in AUTH_EXEMPT_PATHS


def _is_loopback_client(host: str | None) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def _extract_bearer_token(authorization_header: str | None) -> str | None:
    if authorization_header is None:
        return None
    prefix = "Bearer "
    if not authorization_header.startswith(prefix):
        return None
    token = authorization_header.removeprefix(prefix).strip()
    return token or None


def _decode_basic_token(token: str) -> tuple[str, str] | None:
    normalized = token.strip()
    if not normalized:
        return None

    padded = f"{normalized}{'=' * (-len(normalized) % 4)}"
    try:
        decoded_bytes = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):
        return None

    try:
        decoded_text = decoded_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None

    if ":" not in decoded_text:
        return None

    username, password = decoded_text.split(":", 1)
    return username, password


def _extract_basic_credentials(authorization_header: str | None) -> tuple[str, str] | None:
    if authorization_header is None:
        return None
    prefix = "Basic "
    if not authorization_header.startswith(prefix):
        return None
    return _decode_basic_token(authorization_header.removeprefix(prefix))


def _configured_basic_credentials(settings: Settings) -> tuple[str, str] | None:
    username = (settings.api_auth_username or "").strip()
    password = settings.api_auth_password or ""
    if not username or not password:
        return None
    return username, password


def validate_basic_credentials(
    username: str,
    password: str,
    settings: Settings,
) -> tuple[bool, str | None]:
    expected_credentials = _configured_basic_credentials(settings)
    if expected_credentials is None:
        return False, "Username/password auth is enabled but credentials are not configured"

    expected_username, expected_password = expected_credentials
    if not hmac.compare_digest(username, expected_username) or not hmac.compare_digest(
        password,
        expected_password,
    ):
        return False, "Invalid username or password"

    return True, None


def _authorize_transport(
    *,
    client_host: str | None,
    authorization_header: str | None,
    settings: Settings,
    query_params: Mapping[str, str] | None,
    allow_query_params: bool,
) -> tuple[bool, str | None]:
    if settings.api_auth_mode == "disabled":
        return True, None

    if settings.api_auth_mode == "local":
        if _is_loopback_client(client_host):
            return True, None
        return False, "Local mode only allows loopback clients"

    if settings.api_auth_mode == "token":
        expected_token = settings.api_auth_token
        if expected_token is None or not expected_token.strip():
            return False, "API token auth is enabled but no token is configured"

        provided_token = _extract_bearer_token(authorization_header)
        if provided_token is None and allow_query_params and query_params is not None:
            query_token = (query_params.get("token") or "").strip()
            provided_token = query_token or None

        if provided_token is None:
            return False, "Missing bearer token"

        if not hmac.compare_digest(provided_token, expected_token):
            return False, "Invalid bearer token"
        return True, None

    if settings.api_auth_mode == "basic":
        provided_credentials = _extract_basic_credentials(authorization_header)
        if provided_credentials is None and allow_query_params and query_params is not None:
            encoded_credentials = (query_params.get("auth_basic") or "").strip()
            if encoded_credentials:
                provided_credentials = _decode_basic_token(encoded_credentials)

        if provided_credentials is None:
            return False, "Missing basic credentials"

        username, password = provided_credentials
        return validate_basic_credentials(username, password, settings)

    return False, f"Unsupported API auth mode '{settings.api_auth_mode}'"


def is_request_authorized(request: Request, settings: Settings) -> tuple[bool, str | None]:
    client_host = request.client.host if request.client is not None else None
    authorization_header = request.headers.get("Authorization")
    return _authorize_transport(
        client_host=client_host,
        authorization_header=authorization_header,
        settings=settings,
        query_params=None,
        allow_query_params=False,
    )


def is_websocket_authorized(
    websocket: WebSocket,
    settings: Settings,
    *,
    allow_query_params: bool = True,
) -> tuple[bool, str | None]:
    client_host = websocket.client.host if websocket.client is not None else None
    authorization_header = websocket.headers.get("Authorization")
    return _authorize_transport(
        client_host=client_host,
        authorization_header=authorization_header,
        settings=settings,
        query_params=websocket.query_params,
        allow_query_params=allow_query_params,
    )
