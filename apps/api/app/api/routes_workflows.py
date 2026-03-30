from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import SQLModel

from app.core.events import SessionEvent, SessionEventBroker, SessionEventType, get_event_broker
from app.db.models import WorkflowRunDetailRead
from app.workflows.service import (
    SessionNotFoundError,
    WorkflowApprovalRequiredError,
    WorkflowRunNotFoundError,
    WorkflowService,
    WorkflowTemplateNotFoundError,
    get_workflow_service,
)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


class WorkflowStartRequest(SQLModel):
    session_id: str
    seed_message_id: str | None = None


class WorkflowAdvanceRequest(SQLModel):
    approve: bool = False


@router.post("/{template_name}/start", response_model=WorkflowRunDetailRead, status_code=201)
async def start_workflow(
    template_name: str,
    payload: WorkflowStartRequest,
    workflow_service: WorkflowService = Depends(get_workflow_service),
    event_broker: SessionEventBroker = Depends(get_event_broker),
) -> WorkflowRunDetailRead:
    try:
        workflow = workflow_service.start_workflow(
            session_id=payload.session_id,
            template_name=template_name,
            seed_message_id=payload.seed_message_id,
        )
        await _publish_workflow_start_events(event_broker, workflow)
        return workflow
    except SessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found."
        ) from error
    except WorkflowTemplateNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow template not found.",
        ) from error


@router.get("/{run_id}", response_model=WorkflowRunDetailRead)
async def get_workflow(
    run_id: str,
    workflow_service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowRunDetailRead:
    try:
        return workflow_service.get_workflow(run_id)
    except WorkflowRunNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow run not found.",
        ) from error


@router.post("/{run_id}/advance", response_model=WorkflowRunDetailRead)
async def advance_workflow(
    run_id: str,
    payload: WorkflowAdvanceRequest,
    workflow_service: WorkflowService = Depends(get_workflow_service),
    event_broker: SessionEventBroker = Depends(get_event_broker),
) -> WorkflowRunDetailRead:
    try:
        workflow = workflow_service.advance_workflow(
            run_id=run_id,
            approve=payload.approve,
        )
        await _publish_workflow_progress_events(event_broker, workflow)
        return workflow
    except WorkflowApprovalRequiredError as error:
        await _publish_workflow_progress_events(event_broker, error.workflow)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approval required.",
        ) from error
    except WorkflowRunNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow run not found.",
        ) from error


async def _publish_workflow_start_events(
    event_broker: SessionEventBroker,
    workflow: WorkflowRunDetailRead,
) -> None:
    session_id = workflow.session_id
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.WORKFLOW_RUN_STARTED,
            session_id=session_id,
            payload={
                "run_id": workflow.id,
                "template_name": workflow.template_name,
                "status": workflow.status.value,
                "current_stage": workflow.current_stage,
            },
        )
    )
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.WORKFLOW_STAGE_CHANGED,
            session_id=session_id,
            payload={
                "run_id": workflow.id,
                "current_stage": workflow.current_stage,
                "status": workflow.status.value,
            },
        )
    )

    for task in workflow.tasks:
        await event_broker.publish(
            SessionEvent(
                type=SessionEventType.WORKFLOW_TASK_UPDATED,
                session_id=session_id,
                payload={
                    "run_id": workflow.id,
                    "task_id": task.id,
                    "name": task.name,
                    "status": task.status.value,
                    "sequence": task.sequence,
                    "node_type": task.node_type.value,
                    "metadata": dict(task.metadata_payload),
                },
            )
        )

    for graph_type in ("task", "causal"):
        await event_broker.publish(
            SessionEvent(
                type=SessionEventType.GRAPH_UPDATED,
                session_id=session_id,
                payload={
                    "run_id": workflow.id,
                    "graph_type": graph_type,
                    "current_stage": workflow.current_stage,
                },
            )
        )


async def _publish_workflow_progress_events(
    event_broker: SessionEventBroker,
    workflow: WorkflowRunDetailRead,
) -> None:
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.WORKFLOW_STAGE_CHANGED,
            session_id=workflow.session_id,
            payload={
                "run_id": workflow.id,
                "current_stage": workflow.current_stage,
                "status": workflow.status.value,
            },
        )
    )
    for task in workflow.tasks:
        await event_broker.publish(
            SessionEvent(
                type=SessionEventType.WORKFLOW_TASK_UPDATED,
                session_id=workflow.session_id,
                payload={
                    "run_id": workflow.id,
                    "task_id": task.id,
                    "name": task.name,
                    "status": task.status.value,
                    "sequence": task.sequence,
                    "node_type": task.node_type.value,
                    "metadata": dict(task.metadata_payload),
                },
            )
        )
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.GRAPH_UPDATED,
            session_id=workflow.session_id,
            payload={
                "run_id": workflow.id,
                "graph_type": "task",
                "current_stage": workflow.current_stage,
            },
        )
    )
