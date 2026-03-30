from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_chat import router as chat_router
from app.api.routes_graphs import router as graphs_router
from app.api.routes_health import router as health_router
from app.api.routes_mcp import router as mcp_router
from app.api.routes_runtime import router as runtime_router
from app.api.routes_sessions import router as sessions_router
from app.api.routes_settings import router as settings_router
from app.api.routes_skills import router as skills_router
from app.api.routes_workflows import router as workflows_router
from app.core.settings import get_settings
from app.db.session import init_db

settings = get_settings()
LOCAL_DEV_ORIGIN_REGEX = r"^https?://(127\.0\.0\.1|localhost):\d+$"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_origin_regex=LOCAL_DEV_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(health_router)
app.include_router(sessions_router)
app.include_router(chat_router)
app.include_router(runtime_router)
app.include_router(graphs_router)
app.include_router(settings_router)
app.include_router(skills_router)
app.include_router(mcp_router)
app.include_router(workflows_router)
