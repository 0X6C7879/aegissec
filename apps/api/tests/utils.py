from __future__ import annotations

from typing import Any

from httpx import Response


def api_data(response: Response) -> Any:
    payload = response.json()
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload


def api_detail(response: Response) -> str | None:
    payload = response.json()
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, str):
        return detail
    return None
