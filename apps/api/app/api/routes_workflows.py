from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import SQLModel

from app.core.events import SessionEvent, SessionEventBroker, SessionEventType, get_event_broker
from app.db.models import (
    WorkflowRunDetailRead,
    WorkflowRunExportRead,
    WorkflowRunReplayRead,
    WorkflowTemplateRead,
)
from app.workflows.service import (
    SessionNotFoundError,
    WorkflowApprovalRequiredError,
    WorkflowRunNotFoundError,
    WorkflowService,
    WorkflowTaskReorderValidationError,
    WorkflowTemplateNotFoundError,
    get_workflow_service,
)

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


class WorkflowStartRequest(SQLModel):
    session_id: str
    template_name: str | None = None
    seed_message_id: str | None = None


class WorkflowAdvanceRequest(SQLModel):
    approve: bool = False
    user_input: str | None = None
    resume_token: str | None = None
    resolution_payload: dict[str, object] | None = None


class WorkflowTaskPriorityReorderRequest(SQLModel):
    ordered_task_ids: list[str]


@router.get(
    "/templates",
    response_model=list[WorkflowTemplateRead],
    summary="List workflow templates",
    description="Return workflow templates that can be started for a session.",
)
async def list_workflow_templates(
    workflow_service: WorkflowService = Depends(get_workflow_service),
) -> list[WorkflowTemplateRead]:
    return workflow_service.list_workflow_templates()


@router.post(
    "/start",
    response_model=WorkflowRunDetailRead,
    status_code=201,
    summary="Start workflow",
    description="Start a workflow run using an explicit template selection payload.",
)
async def start_workflow_with_selection(
    payload: WorkflowStartRequest,
    workflow_service: WorkflowService = Depends(get_workflow_service),
    event_broker: SessionEventBroker = Depends(get_event_broker),
) -> WorkflowRunDetailRead:
    template_name = payload.template_name or "authorized-assessment"
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


@router.post(
    "/{template_name}/start",
    response_model=WorkflowRunDetailRead,
    status_code=201,
    summary="Start workflow by template",
    description="Start a workflow run for a session using the template encoded in the route.",
)
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


@router.get(
    "/{run_id}",
    response_model=WorkflowRunDetailRead,
    summary="Get workflow run",
    description="Return the current workflow run state, task DAG, and execution metadata.",
)
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


@router.post(
    "/{run_id}/advance",
    response_model=WorkflowRunDetailRead,
    summary="Advance workflow run",
    description="Advance the workflow state machine, optionally satisfying an approval gate.",
)
async def advance_workflow(
    run_id: str,
    payload: WorkflowAdvanceRequest,
    workflow_service: WorkflowService = Depends(get_workflow_service),
    event_broker: SessionEventBroker = Depends(get_event_broker),
) -> WorkflowRunDetailRead:
    try:
        workflow = await workflow_service.advance_workflow(
            run_id=run_id,
            approve=payload.approve,
            user_input=payload.user_input,
            resume_token=payload.resume_token,
            resolution_payload=(
                dict(payload.resolution_payload)
                if isinstance(payload.resolution_payload, dict)
                else None
            ),
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


@router.post("/{run_id}/tasks/reorder-priority", response_model=WorkflowRunDetailRead)
async def reorder_workflow_sibling_task_priorities(
    run_id: str,
    payload: WorkflowTaskPriorityReorderRequest,
    workflow_service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowRunDetailRead:
    try:
        return workflow_service.reorder_sibling_task_priorities(
            run_id,
            ordered_task_ids=list(payload.ordered_task_ids),
        )
    except WorkflowRunNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow run not found.",
        ) from error
    except WorkflowTaskReorderValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ordered_task_ids must include all and only sibling task IDs in desired order.",
        ) from error


@router.get("/{run_id}/export", response_model=WorkflowRunExportRead)
async def export_workflow_run(
    run_id: str,
    workflow_service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowRunExportRead:
    try:
        return workflow_service.export_workflow_run(run_id)
    except WorkflowRunNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow run not found.",
        ) from error


@router.get("/{run_id}/replay", response_model=WorkflowRunReplayRead)
async def replay_workflow_run(
    run_id: str,
    workflow_service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowRunReplayRead:
    try:
        return workflow_service.replay_workflow_run(run_id)
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
        await event_broker.publish(
            SessionEvent(
                type=SessionEventType.TASK_PLANNED,
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
    if workflow.status.value == "needs_approval":
        await event_broker.publish(
            SessionEvent(
                type=SessionEventType.APPROVAL_REQUIRED,
                session_id=workflow.session_id,
                payload={
                    "run_id": workflow.id,
                    "current_stage": workflow.current_stage,
                },
            )
        )

    for graph_type in ("task", "evidence", "causal", "attack"):
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
    batch_executions = _batch_execution_records(workflow)
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
    for execution in batch_executions:
        task_id = execution.get("task_node_id")
        if isinstance(task_id, str):
            matched_task = next(
                (candidate for candidate in workflow.tasks if candidate.id == task_id), None
            )
            if matched_task is not None:
                base_payload = {
                    "run_id": workflow.id,
                    "task_id": matched_task.id,
                    "name": matched_task.name,
                    "status": matched_task.status.value,
                    "sequence": matched_task.sequence,
                    "node_type": matched_task.node_type.value,
                    "metadata": dict(matched_task.metadata_payload),
                    "trace_id": execution.get("id"),
                }
                await event_broker.publish(
                    SessionEvent(
                        type=SessionEventType.TASK_STARTED,
                        session_id=workflow.session_id,
                        payload=base_payload,
                    )
                )
                await event_broker.publish(
                    SessionEvent(
                        type=SessionEventType.TASK_FINISHED,
                        session_id=workflow.session_id,
                        payload=base_payload,
                    )
                )
    if workflow.status.value == "needs_approval":
        await event_broker.publish(
            SessionEvent(
                type=SessionEventType.APPROVAL_REQUIRED,
                session_id=workflow.session_id,
                payload={
                    "run_id": workflow.id,
                    "current_stage": workflow.current_stage,
                },
            )
        )
    for graph_type in ("task", "evidence", "causal", "attack"):
        await event_broker.publish(
            SessionEvent(
                type=SessionEventType.GRAPH_UPDATED,
                session_id=workflow.session_id,
                payload={
                    "run_id": workflow.id,
                    "graph_type": graph_type,
                    "current_stage": workflow.current_stage,
                },
            )
        )


def _latest_execution_record(workflow: WorkflowRunDetailRead) -> dict[str, object] | None:
    state = workflow.state_payload
    records = state.get("execution_records", [])
    if not isinstance(records, list) or not records:
        return None
    latest = records[-1]
    if not isinstance(latest, dict):
        return None
    return latest


def _batch_execution_records(workflow: WorkflowRunDetailRead) -> list[dict[str, object]]:
    state = workflow.state_payload
    raw_records = state.get("execution_records", [])
    if not isinstance(raw_records, list):
        return []
    records = [record for record in raw_records if isinstance(record, dict)]
    if not records:
        return []

    batch_raw = state.get("batch")
    batch = dict(batch_raw) if isinstance(batch_raw, dict) else {}
    executed_task_ids = [
        task_id for task_id in batch.get("executed_task_ids", []) if isinstance(task_id, str)
    ]
    if not executed_task_ids:
        latest = _latest_execution_record(workflow)
        return [latest] if latest is not None else []

    executed_task_id_set = set(executed_task_ids)
    batch_cycle = batch.get("cycle") if isinstance(batch.get("cycle"), int) else None

    matched_records = [
        record
        for record in records
        if isinstance(record.get("task_node_id"), str)
        and record.get("task_node_id") in executed_task_id_set
        and (batch_cycle is None or record.get("batch_cycle") == batch_cycle)
    ]
    if matched_records:
        return matched_records

    fallback_records = [
        record
        for record in records
        if isinstance(record.get("task_node_id"), str)
        and record.get("task_node_id") in executed_task_id_set
    ]
    if fallback_records:
        return fallback_records[-len(executed_task_ids) :]

    return records[-len(executed_task_ids) :]
