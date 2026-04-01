from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.api import ApiError, ApiErrorResponse, ApiResponse, ResponseMeta
from app.core.auth import is_api_request, is_auth_exempt_path, is_request_authorized
from app.core.request_context import set_request_id
from app.core.settings import get_settings

logger = logging.getLogger("aegissec.api")


class APIRequestMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        set_request_id(request_id)
        request.state.request_id = request_id
        started_at = perf_counter()
        settings = getattr(request.app.state, "settings", get_settings())

        if is_api_request(request.url.path) and not is_auth_exempt_path(request.url.path):
            authorized, reason = is_request_authorized(request, settings)
            if not authorized:
                payload = ApiErrorResponse(
                    detail=reason or "Unauthorized",
                    error=ApiError(code="unauthorized", message=reason or "Unauthorized"),
                    meta=ResponseMeta(request_id=request_id),
                )
                unauthorized_response = JSONResponse(
                    status_code=401,
                    content=payload.model_dump(mode="json"),
                )
                unauthorized_response.headers["X-Request-ID"] = request_id
                return unauthorized_response

        response = await call_next(request)
        duration_ms = round((perf_counter() - started_at) * 1000, 2)
        response.headers["X-Request-ID"] = request_id

        if is_api_request(request.url.path):
            self._capture_request_log(request, response.status_code, duration_ms, request_id)
            response = await self._wrap_success_response(request, response, request_id)

        return response

    @staticmethod
    def _capture_request_log(
        request: Request,
        status_code: int,
        duration_ms: float,
        request_id: str,
    ) -> None:
        session_id = request.path_params.get("session_id")
        logger.info(
            "%s %s -> %s (%.2f ms) [request_id=%s]",
            request.method,
            request.url.path,
            status_code,
            duration_ms,
            request_id,
        )

        request.state.pending_request_log = {
            "session_id": session_id if isinstance(session_id, str) else None,
            "level": "info",
            "source": "api",
            "event_type": "request.completed",
            "message": f"{request.method} {request.url.path}",
            "payload": {
                "status_code": status_code,
                "duration_ms": duration_ms,
                "request_id": request_id,
            },
        }

    @staticmethod
    async def _wrap_success_response(
        request: Request,
        response: Response,
        request_id: str,
    ) -> Response:
        if response.status_code >= 400 or response.status_code == 204:
            return response

        media_type = response.media_type or response.headers.get("content-type", "")
        if "application/json" not in media_type:
            return response

        body_iterator = getattr(response, "body_iterator", None)
        if body_iterator is None:
            return response

        body = b""
        async for chunk in body_iterator:
            body += chunk

        if not body:
            return response

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return response
        if isinstance(payload, dict) and "data" in payload and "meta" in payload:
            wrapped_payload = payload
        else:
            wrapped_payload = ApiResponse[Any](
                data=payload,
                meta=ResponseMeta(request_id=request_id),
            ).model_dump(mode="json")

        return JSONResponse(
            status_code=response.status_code,
            content=wrapped_payload,
            headers={
                key: value
                for key, value in response.headers.items()
                if key.lower() != "content-length"
            },
        )
