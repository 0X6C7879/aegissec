from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError
from sqlmodel import Session as DBSession

from app.core.api import PaginationMeta, SortMeta, ok_response
from app.db.models import (
    RuntimeContainerStateRead,
    RuntimeContainerStatus,
    RuntimeExecuteRequest,
    RuntimeExecutionRunRead,
    RuntimePolicy,
    RuntimeProfileRead,
    RuntimeStatusRead,
    to_runtime_artifact_read,
    to_runtime_execution_run_read,
)
from app.db.repositories import RuntimeRepository, SessionRepository
from app.db.session import get_db_session
from app.services.runtime import (
    RuntimeArtifactPathError,
    RuntimeOperationError,
    RuntimePolicyViolationError,
    RuntimeService,
    get_runtime_service,
)

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


class RuntimeHealthRead(BaseModel):
    status: str
    runtime_status: RuntimeContainerStatus
    container_name: str
    image: str
    container_id: str | None = None
    workspace_host_path: str
    workspace_container_path: str
    started_at: str | None = None


def _ensure_session_exists(db_session: DBSession, session_id: str) -> None:
    repository = SessionRepository(db_session)
    session = repository.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")


@router.get(
    "/status",
    response_model=RuntimeStatusRead,
    summary="Get runtime status",
    description="Inspect current runtime container health and recent command execution history.",
)
async def get_runtime_status(
    runtime_service: RuntimeService = Depends(get_runtime_service),
) -> RuntimeStatusRead:
    try:
        return runtime_service.get_status()
    except RuntimeOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get(
    "/health",
    response_model=RuntimeHealthRead,
    summary="Get runtime health",
    description="Return a lightweight runtime health snapshot for readiness checks.",
)
async def get_runtime_health(
    runtime_service: RuntimeService = Depends(get_runtime_service),
) -> object:
    try:
        runtime_status = runtime_service.get_status().runtime
        health_status = (
            "ok" if runtime_status.status != RuntimeContainerStatus.MISSING else "degraded"
        )
        return ok_response(
            RuntimeHealthRead(
                status=health_status,
                runtime_status=runtime_status.status,
                container_name=runtime_status.container_name,
                image=runtime_status.image,
                container_id=runtime_status.container_id,
                workspace_host_path=runtime_status.workspace_host_path,
                workspace_container_path=runtime_status.workspace_container_path,
                started_at=(
                    runtime_status.started_at.isoformat()
                    if runtime_status.started_at is not None
                    else None
                ),
            ).model_dump(mode="json")
        )
    except RuntimeOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get(
    "/runs",
    summary="List runtime runs",
    description=(
        "Return runtime execution records with optional session filtering, pagination, and search."
    ),
)
async def list_runtime_runs(
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    session_id: str | None = None,
    sort_by: Literal["started_at", "created_at"] = "started_at",
    sort_order: Literal["asc", "desc"] = "desc",
    db_session: DBSession = Depends(get_db_session),
) -> object:
    repository = RuntimeRepository(db_session)
    offset = (page - 1) * page_size
    runs = repository.list_runs(
        session_id=session_id,
        query=q,
        offset=offset,
        limit=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    total = repository.count_runs(session_id=session_id, query=q)
    return ok_response(
        [
            to_runtime_execution_run_read(
                run, repository.list_artifacts_for_run(run.id)
            ).model_dump(mode="json")
            for run in runs
        ],
        pagination=PaginationMeta(page=page, page_size=page_size, total=total),
        sort=SortMeta(by=sort_by, direction=sort_order),
    )


@router.get(
    "/artifacts",
    summary="List runtime artifacts",
    description=(
        "Return persisted runtime artifacts with optional session filtering, "
        "pagination, and search."
    ),
)
async def list_runtime_artifacts(
    page: int = 1,
    page_size: int = 20,
    q: str | None = None,
    session_id: str | None = None,
    sort_by: Literal["created_at", "relative_path"] = "created_at",
    sort_order: Literal["asc", "desc"] = "desc",
    db_session: DBSession = Depends(get_db_session),
) -> object:
    repository = RuntimeRepository(db_session)
    offset = (page - 1) * page_size
    artifacts = repository.list_artifacts(
        session_id=session_id,
        query=q,
        offset=offset,
        limit=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    total = repository.count_artifacts(session_id=session_id, query=q)
    return ok_response(
        [to_runtime_artifact_read(artifact).model_dump(mode="json") for artifact in artifacts],
        pagination=PaginationMeta(page=page, page_size=page_size, total=total),
        sort=SortMeta(by=sort_by, direction=sort_order),
    )


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
    runtime_policy: RuntimePolicy | None = None
    if payload.session_id is not None:
        session = SessionRepository(db_session).get_session(payload.session_id)
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        try:
            runtime_policy = runtime_service.resolve_policy_for_session(session)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid session runtime policy: {exc.errors()[0]['msg']}",
            ) from exc

    try:
        return runtime_service.execute(payload, runtime_policy=runtime_policy)
    except RuntimeArtifactPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimePolicyViolationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.get("/profiles", response_model=list[RuntimeProfileRead])
async def list_runtime_profiles(
    runtime_service: RuntimeService = Depends(get_runtime_service),
) -> list[RuntimeProfileRead]:
    return runtime_service.list_profiles()


@router.post("/upload", response_model=RuntimeExecutionRunRead)
async def upload_runtime_artifact(
    file: UploadFile = File(...),
    path: str = Form(...),
    session_id: str | None = Form(default=None),
    overwrite: bool = Form(default=False),
    runtime_service: RuntimeService = Depends(get_runtime_service),
    db_session: DBSession = Depends(get_db_session),
) -> RuntimeExecutionRunRead:
    if session_id is not None:
        _ensure_session_exists(db_session, session_id)
    try:
        content = await file.read()
        return runtime_service.upload_artifact(
            destination_path=path,
            content=content,
            session_id=session_id,
            overwrite=overwrite,
        )
    except RuntimeArtifactPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/download")
async def download_runtime_artifact(
    path: str = Query(...),
    runtime_service: RuntimeService = Depends(get_runtime_service),
) -> Response:
    try:
        file_path, content = runtime_service.download_artifact_bytes(artifact_path=path)
    except RuntimeArtifactPathError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={file_path.name}"},
    )


@router.post("/artifacts/cleanup")
async def cleanup_runtime_artifacts(
    runtime_service: RuntimeService = Depends(get_runtime_service),
) -> object:
    return ok_response(runtime_service.cleanup_artifacts())


@router.post("/runs/clear")
async def clear_runtime_runs(
    runtime_service: RuntimeService = Depends(get_runtime_service),
) -> object:
    return ok_response(runtime_service.clear_runs())
