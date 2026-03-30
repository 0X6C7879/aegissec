from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.db.models import GraphType, SessionGraphRead
from app.graphs.service import (
    GraphService,
    SessionNotFoundError,
    WorkflowGraphNotFoundError,
    get_graph_service,
)

router = APIRouter(prefix="/api/sessions/{session_id}/graphs", tags=["graphs"])


@router.get("/task", response_model=SessionGraphRead)
async def get_task_graph(
    session_id: str,
    graph_service: GraphService = Depends(get_graph_service),
) -> SessionGraphRead:
    try:
        return graph_service.get_graph(session_id=session_id, graph_type=GraphType.TASK)
    except SessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        ) from error
    except WorkflowGraphNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow run not found.",
        ) from error


@router.get("/causal", response_model=SessionGraphRead)
async def get_causal_graph(
    session_id: str,
    graph_service: GraphService = Depends(get_graph_service),
) -> SessionGraphRead:
    try:
        return graph_service.get_graph(session_id=session_id, graph_type=GraphType.CAUSAL)
    except SessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
        ) from error
    except WorkflowGraphNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow run not found.",
        ) from error
