from __future__ import annotations

from typing import TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.core.request_context import get_request_id

T = TypeVar("T")


class PaginationMeta(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)


class SortMeta(BaseModel):
    by: str
    direction: str


class ResponseMeta(BaseModel):
    request_id: str | None = None
    pagination: PaginationMeta | None = None
    sort: SortMeta | None = None


class ApiResponse[T](BaseModel):
    data: T
    meta: ResponseMeta | None = None


class ApiError(BaseModel):
    code: str
    message: str


class ApiErrorResponse(BaseModel):
    detail: str
    error: ApiError
    meta: ResponseMeta | None = None


class AckResponse(BaseModel):
    ok: bool = True


def build_meta(
    *,
    pagination: PaginationMeta | None = None,
    sort: SortMeta | None = None,
) -> ResponseMeta:
    return ResponseMeta(request_id=get_request_id(), pagination=pagination, sort=sort)


def ok_response(
    data: object,
    *,
    status_code: int = 200,
    pagination: PaginationMeta | None = None,
    sort: SortMeta | None = None,
) -> JSONResponse:
    payload = ApiResponse[object](data=data, meta=build_meta(pagination=pagination, sort=sort))
    return JSONResponse(status_code=status_code, content=payload.model_dump(mode="json"))
