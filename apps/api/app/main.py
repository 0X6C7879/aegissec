import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import Session as DBSession

from app.api.routes_auth import router as auth_router
from app.api.routes_chat import router as chat_router
from app.api.routes_graphs import router as graphs_router
from app.api.routes_graphs import run_router as workflow_graphs_router
from app.api.routes_health import router as health_router
from app.api.routes_mcp import router as mcp_router
from app.api.routes_projects import router as projects_router
from app.api.routes_runtime import router as runtime_router
from app.api.routes_sessions import router as sessions_router
from app.api.routes_settings import router as settings_router
from app.api.routes_skills import router as skills_router
from app.core.errors import register_exception_handlers
from app.core.events import get_event_broker
from app.core.logging_middleware import APIRequestMiddleware
from app.core.settings import get_settings
from app.db.session import engine, init_db
from app.services.session_generation import recover_abandoned_generations

settings = get_settings()
LOCAL_DEV_ORIGIN_REGEX = r"^https?://(127\.0\.0\.1|localhost):\d+$"
OPENAPI_TAGS = [
    {"name": "health", "description": "Platform and runtime health visibility."},
    {"name": "auth", "description": "Local-first authentication mode introspection."},
    {"name": "projects", "description": "Project records and project-scoped defaults."},
    {"name": "sessions", "description": "Session lifecycle, history, and artifact access."},
    {"name": "chat", "description": "Session chat execution and websocket-driven collaboration."},
    {
        "name": "runtime",
        "description": "Runtime container control, command execution, and artifacts.",
    },
    {"name": "graphs", "description": "Task, evidence, and causal graph snapshots."},
    {"name": "settings", "description": "User-scoped settings backed by local environment files."},
    {"name": "skills", "description": "Skill discovery, scanning, and content lookup."},
    {"name": "mcp", "description": "MCP server import, inspection, and capability management."},
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    get_event_broker().configure_persistence(lambda: DBSession(engine))
    recover_abandoned_generations(engine)
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    summary="Local-first defensive security workbench API",
    description=(
        "FastAPI backend for authorized security research, runtime execution, "
        "session history, graph persistence, and project-scoped coordination."
    ),
    contact={"name": "AegisSec Local Workspace"},
    openapi_tags=OPENAPI_TAGS,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)
app.state.database_engine = engine
app.state.settings = settings
logging.basicConfig(level=logging.INFO)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_origin_regex=LOCAL_DEV_ORIGIN_REGEX,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(APIRequestMiddleware)
register_exception_handlers(app)
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(sessions_router)
app.include_router(chat_router)
app.include_router(runtime_router)
app.include_router(graphs_router)
app.include_router(workflow_graphs_router)
app.include_router(settings_router)
app.include_router(skills_router)
app.include_router(mcp_router)
