from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.api import ApiError, ApiErrorResponse, ResponseMeta
from app.core.request_context import get_request_id

logger = logging.getLogger("aegissec.api")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str | dict) else "Request failed"
        message = detail.get("message", "Request failed") if isinstance(detail, dict) else detail
        payload = ApiErrorResponse(
            detail=detail,
            error=ApiError(code=f"http_{exc.status_code}", message=message),
            meta=ResponseMeta(request_id=get_request_id()),
        )
        return JSONResponse(status_code=exc.status_code, content=payload.model_dump(mode="json"))

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "Request validation failed"
        payload = ApiErrorResponse(
            detail=detail,
            error=ApiError(code="validation_error", message=detail),
            meta=ResponseMeta(request_id=get_request_id()),
        )
        logger.warning("Validation error: %s", exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=payload.model_dump(mode="json"),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled API error", exc_info=exc)
        detail = "Internal server error"
        payload = ApiErrorResponse(
            detail=detail,
            error=ApiError(code="internal_server_error", message=detail),
            meta=ResponseMeta(request_id=get_request_id()),
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=payload.model_dump(mode="json"),
        )
