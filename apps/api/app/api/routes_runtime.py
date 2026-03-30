from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session as DBSession

from app.db.models import (
    RuntimeContainerStateRead,
    RuntimeExecuteRequest,
    RuntimeExecutionRunRead,
    RuntimeStatusRead,
)
from app.db.repositories import SessionRepository
from app.db.session import get_db_session
from app.services.runtime import (
    RuntimeArtifactPathError,
    RuntimeOperationError,
    RuntimeService,
    get_runtime_service,
)

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


def _ensure_session_exists(db_session: DBSession, session_id: str) -> None:
    repository = SessionRepository(db_session)
    session = repository.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")


@router.get("/status", response_model=RuntimeStatusRead)
async def get_runtime_status(
    runtime_service: RuntimeService = Depends(get_runtime_service),
) -> RuntimeStatusRead:
    try:
        return runtime_service.get_status()
    except RuntimeOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post("/start", response_model=RuntimeContainerStateRead)
async def start_runtime(
    runtime_service: RuntimeService = Depends(get_runtime_service),
) -> RuntimeContainerStateRead:
    try:
        return runtime_service.start()
    except RuntimeOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post("/stop", response_model=RuntimeContainerStateRead)
async def stop_runtime(
    runtime_service: RuntimeService = Depends(get_runtime_service),
) -> RuntimeContainerStateRead:
    try:
        return runtime_service.stop()
    except RuntimeOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post("/execute", response_model=RuntimeExecutionRunRead)
async def execute_runtime_command(
    payload: RuntimeExecuteRequest,
    db_session: DBSession = Depends(get_db_session),
    runtime_service: RuntimeService = Depends(get_runtime_service),
) -> RuntimeExecutionRunRead:
    if payload.session_id is not None:
        _ensure_session_exists(db_session, payload.session_id)

    try:
        return runtime_service.execute(payload)
    except RuntimeArtifactPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
