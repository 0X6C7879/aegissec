from fastapi import APIRouter
from pydantic import BaseModel

from app.core.settings import get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    name: str
    status: str
    version: str


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        name=settings.app_name,
        status="ok",
        version=settings.app_version,
    )
