from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from app.agent.continuation import ContinuationContract, ContinuationResolution
from app.agent.continuation_store import ContinuationStore
from app.core.events import SessionEvent, SessionEventBroker, SessionEventType
from app.db.models import (
    AssistantTranscriptSegmentKind,
    ChatGeneration,
    GenerationStatus,
    Message,
    Session,
    SessionStatus,
    utc_now,
)
from app.db.repositories import SessionRepository
from app.services.chat_runtime import ToolCallRequest
from app.services.session_generation import GenerationPausedError, SessionGenerationManager


@dataclass(frozen=True)
class ContinuationLookup:
    generation: ChatGeneration
    pause_state: dict[str, object]
    contract: ContinuationContract


@dataclass(frozen=True)
class ContinuationResolveInput:
    approved: bool
    user_input: str | None
    resolution_payload: dict[str, object]


@dataclass(frozen=True)
class ResolvedContinuation:
    session: Session
    continuation_token: str
    contract: ContinuationContract
    resolution: ContinuationResolution


class ContinuationResolutionError(Exception):
    def __init__(self, *, status_code: int, detail: object) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


class AssistantTracePublisher(Protocol):
    async def __call__(
        self,
        repository: SessionRepository,
        event_broker: SessionEventBroker,
        *,
        session_id: str,
        assistant_message: Message,
        entry: dict[str, object],
    ) -> None: ...


def normalize_continuation_resolution_input(
    *,
    approve: bool | None,
    approved: bool | None,
    scope_confirmed: bool | None,
    user_input: str | None,
    resolution_payload: dict[str, object] | None,
) -> ContinuationResolveInput:
    payload = dict(resolution_payload) if isinstance(resolution_payload, dict) else {}
    resolved_approved = approved if approved is not None else approve
    if resolved_approved is None:
        resolved_approved = True
    if scope_confirmed is not None and "scope_confirmed" not in payload:
        payload["scope_confirmed"] = scope_confirmed
    return ContinuationResolveInput(
        approved=resolved_approved,
        user_input=user_input,
        resolution_payload=payload,
    )


def find_generation_for_continuation(
    repository: SessionRepository,
    session_id: str,
    continuation_token: str,
) -> ContinuationLookup | None:
    store = ContinuationStore()
    for generation in repository.list_generations(session_id):
        raw_pause_state = generation.metadata_json.get("pause_state")
        if not isinstance(raw_pause_state, dict):
            continue
        pause_state = dict(raw_pause_state)
        store.ensure_pause_state(pause_state)
        contract = store.continuation_for_token_any(
            pause_state,
            continuation_token=continuation_token,
        )
        if contract is not None:
            return ContinuationLookup(
                generation=generation,
                pause_state=pause_state,
                contract=contract,
            )
    return None


def clear_generation_continuation_state(
    repository: SessionRepository,
    generation: ChatGeneration,
    assistant_message: Message | None,
    *,
    abort_reason: str | None = None,
) -> None:
    store = ContinuationStore()

    generation_metadata = dict(generation.metadata_json)
    raw_pause_state = generation_metadata.get("pause_state")
    pause_state = dict(raw_pause_state) if isinstance(raw_pause_state, dict) else None
    if pause_state is not None:
        store.ensure_pause_state(pause_state)
        if abort_reason:
            for contract in list(store.active_continuations(pause_state)):
                continuation_token = contract.continuation_token
                if continuation_token:
                    store.abort_continuation(
                        pause_state,
                        continuation_token=continuation_token,
                        reason=abort_reason,
                    )
        generation_metadata["pause_state"] = pause_state
    generation_metadata.pop("pending_continuation", None)
    repository.update_generation(generation, metadata_json=generation_metadata)

    if assistant_message is None:
        return

    assistant_metadata = dict(assistant_message.metadata_json)
    if pause_state is not None:
        assistant_metadata["pause_state"] = pause_state
    assistant_metadata.pop("pending_continuation", None)
    repository.update_message(assistant_message, metadata_json=assistant_metadata)


async def resolve_session_continuation(
    *,
    repository: SessionRepository,
    session: Session,
    continuation_token: str,
    request: ContinuationResolveInput,
    event_broker: SessionEventBroker,
    generation_manager: SessionGenerationManager,
) -> ResolvedContinuation:
    lookup = find_generation_for_continuation(repository, session.id, continuation_token)
    if lookup is None:
        raise ContinuationResolutionError(status_code=404, detail="Continuation not found")

    generation = lookup.generation
    pause_state = lookup.pause_state
    contract = lookup.contract
    protocol_payload = (
        dict(contract.protocol_payload) if isinstance(contract.protocol_payload, dict) else {}
    )
    action = (
        protocol_payload.get("action") if isinstance(protocol_payload.get("action"), str) else None
    )

    if contract.continuation_status == "resolved":
        raise ContinuationResolutionError(
            status_code=409,
            detail={
                "message": "Continuation already resolved.",
                "continuation_token": continuation_token,
                "error": "already_resolved",
                "action": action,
            },
        )
    if contract.continuation_status == "aborted":
        raise ContinuationResolutionError(
            status_code=409,
            detail={
                "message": "Continuation already aborted.",
                "continuation_token": continuation_token,
                "error": "already_aborted",
                "action": action,
            },
        )

    assistant_message = repository.get_message(generation.assistant_message_id)
    if assistant_message is None:
        raise ContinuationResolutionError(
            status_code=404,
            detail="Assistant message not found for continuation.",
        )

    continuation_future_active = await generation_manager.has_continuation_future(
        session.id,
        continuation_token,
    )
    if (
        session.status != SessionStatus.PAUSED
        or generation.status != GenerationStatus.RUNNING
        or not continuation_future_active
    ):
        clear_generation_continuation_state(
            repository,
            generation,
            assistant_message,
            abort_reason="Continuation is no longer resumable.",
        )
        raise ContinuationResolutionError(
            status_code=409,
            detail={
                "message": "Continuation is no longer resumable.",
                "continuation_token": continuation_token,
                "error": "not_resumable",
                "action": action,
            },
        )

    store = ContinuationStore()
    store.ensure_pause_state(pause_state)
    resolved_contract, resolution, validation_error = store.resolve_continuation(
        pause_state,
        continuation_token=continuation_token,
        approve=request.approved,
        user_input=request.user_input,
        resolution_payload=request.resolution_payload,
    )
    if validation_error is not None or resolved_contract is None or resolution is None:
        raise ContinuationResolutionError(
            status_code=400,
            detail={
                "message": validation_error or "Unable to resolve continuation.",
                "continuation_token": continuation_token,
                "error": validation_error or "resolution_failed",
                "action": action,
            },
        )

    resolution_state = resolution.to_state()

    generation_metadata = dict(generation.metadata_json)
    generation_metadata["pause_state"] = pause_state
    generation_metadata["continuation_resolution"] = resolution_state
    generation_metadata.pop("pending_continuation", None)
    repository.update_generation(generation, metadata_json=generation_metadata)

    assistant_metadata = dict(assistant_message.metadata_json)
    assistant_metadata["pause_state"] = pause_state
    assistant_metadata["continuation_resolution"] = resolution_state
    assistant_metadata.pop("pending_continuation", None)
    repository.update_message(assistant_message, metadata_json=assistant_metadata)

    updated_session = repository.update_session(session, status=SessionStatus.RUNNING)
    await generation_manager.resolve_continuation_future(
        session.id,
        continuation_token,
        resolution_state,
    )

    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.WORKFLOW_TASK_UPDATED,
            session_id=session.id,
            payload={
                "continuation_token": continuation_token,
                "status": "resolved",
                "protocol_kind": resolved_contract.protocol_kind,
                "tool_name": resolved_contract.tool_name,
                "task_id": resolved_contract.task_id,
                "resolution": resolution_state,
            },
        )
    )
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_UPDATED,
            session_id=updated_session.id,
            payload={"title": updated_session.title, "status": updated_session.status.value},
        )
    )

    return ResolvedContinuation(
        session=updated_session,
        continuation_token=continuation_token,
        contract=resolved_contract,
        resolution=resolution,
    )


async def pause_tool_for_governance(
    *,
    repository: SessionRepository,
    session: Session,
    assistant_message: Message,
    tool_request: ToolCallRequest,
    decision: Any,
    governance_metadata: dict[str, object] | None,
    tool_call_metadata: dict[str, object],
    started_payload: dict[str, object],
    event_broker: SessionEventBroker,
    generation_manager: SessionGenerationManager,
    publish_assistant_trace: AssistantTracePublisher,
) -> dict[str, object]:
    if assistant_message.generation_id is None:
        raise RuntimeError("Tool pause requires an active generation.")

    transcript = importlib.import_module("app.harness.transcript")
    generation_events = importlib.import_module("app.harness.generation_events")

    generation = repository.get_generation(assistant_message.generation_id)
    if generation is None:
        raise RuntimeError("Generation record was not found for governance pause.")

    raw_pause_state = generation.metadata_json.get("pause_state")
    pause_state = dict(raw_pause_state) if isinstance(raw_pause_state, dict) else {}
    store = ContinuationStore()
    store.ensure_pause_state(pause_state)

    action = decision.action
    protocol_kind = "approval" if action == "require_approval" else "interaction"
    continuation_reason = decision.reason or "Tool use requires operator action."
    resume_payload_schema: dict[str, object] = (
        {"required_fields": ["approved"]}
        if protocol_kind == "approval"
        else {"required_fields": ["scope_confirmed"]}
    )
    protocol_payload = {
        "action": action,
        "tool": tool_request.tool_name,
        "tool_call_id": tool_request.tool_call_id,
        "arguments": dict(tool_request.arguments),
        "reason": continuation_reason,
        "governance": governance_metadata,
    }
    contract = ContinuationContract(
        continuation_token=str(uuid4()),
        protocol_kind=protocol_kind,
        task_id=assistant_message.generation_id,
        task_name=f"tool:{tool_request.tool_name}",
        tool_name=tool_request.tool_name,
        originating_turn_id=assistant_message.id,
        originating_delta_id=tool_request.tool_call_id,
        originating_trace_id=tool_request.tool_call_id,
        resume_payload_schema=resume_payload_schema,
        protocol_payload=protocol_payload,
        continuation_status="pending",
        continuation_reason=continuation_reason,
        created_at=utc_now().isoformat(),
    )
    contract = store.register_continuation(pause_state, contract)
    continuation_state = contract.to_state()

    generation_metadata = dict(generation.metadata_json)
    generation_metadata["pause_state"] = pause_state
    generation_metadata["pending_continuation"] = continuation_state
    repository.update_generation(generation, metadata_json=generation_metadata)

    assistant_metadata = dict(assistant_message.metadata_json)
    assistant_metadata["pause_state"] = pause_state
    assistant_metadata["pending_continuation"] = continuation_state
    repository.update_message(assistant_message, metadata_json=assistant_metadata)

    transcript_segments = transcript.message_transcript_segments(repository, assistant_message)
    tool_call_segment = transcript.find_transcript_segment(
        transcript_segments,
        kind=AssistantTranscriptSegmentKind.TOOL_CALL,
        tool_call_id=tool_request.tool_call_id,
    )
    if tool_call_segment is not None:
        transcript.update_transcript_segment(
            repository,
            assistant_message=assistant_message,
            segment=tool_call_segment,
            status="blocked",
            metadata_json={
                **dict(tool_call_metadata),
                "governance_action": action,
                "continuation": continuation_state,
            },
        )
    transcript.append_transcript_segment(
        repository,
        assistant_message=assistant_message,
        kind=AssistantTranscriptSegmentKind.STATUS,
        status="blocked",
        title=("等待审批" if action == "require_approval" else "等待范围确认"),
        text=continuation_reason,
        tool_name=tool_request.tool_name,
        tool_call_id=tool_request.tool_call_id,
        metadata_json={
            "action": action,
            "continuation": continuation_state,
            "reason": continuation_reason,
        },
    )

    tool_step = repository.get_open_generation_step(
        assistant_message.generation_id,
        kind="tool",
        tool_call_id=tool_request.tool_call_id,
    )
    if tool_step is not None:
        repository.update_generation_step(
            tool_step,
            phase="tool_wait",
            status="blocked",
            state="paused",
            safe_summary=continuation_reason,
            metadata_json={
                **dict(tool_step.metadata_json),
                "governance_action": action,
                "continuation": continuation_state,
            },
        )

    updated_session = repository.update_session(session, status=SessionStatus.PAUSED)
    await publish_assistant_trace(
        repository,
        event_broker,
        session_id=session.id,
        assistant_message=assistant_message,
        entry={
            "state": "tool.paused",
            "tool": tool_request.tool_name,
            "tool_call_id": tool_request.tool_call_id,
            "action": action,
            "reason": continuation_reason,
            "continuation_token": contract.continuation_token,
        },
    )
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.APPROVAL_REQUIRED,
            session_id=session.id,
            payload={
                **started_payload,
                "action": action,
                "reason": continuation_reason,
                "continuation_token": contract.continuation_token,
                "protocol_kind": protocol_kind,
                "resume_payload_schema": resume_payload_schema,
                "protocol_payload": protocol_payload,
            },
        )
    )
    await generation_events.publish_message_event(
        event_broker,
        event_type=SessionEventType.MESSAGE_UPDATED,
        session_id=session.id,
        message=assistant_message,
    )
    await generation_events.publish_session_updated(
        event_broker,
        updated_session,
        queued_prompt_count=repository.queue_size(updated_session.id),
    )

    continuation_future = await generation_manager.register_continuation_future(
        session.id,
        contract.continuation_token,
    )
    await generation_manager.reject_future(
        session.id,
        assistant_message.generation_id,
        GenerationPausedError(
            continuation_reason,
            continuation_token=contract.continuation_token,
            action=action,
        ),
    )
    resolution_payload = await continuation_future

    refreshed_message = repository.get_message(assistant_message.id)
    if refreshed_message is not None:
        assistant_message.metadata_json = dict(refreshed_message.metadata_json)

    if tool_call_segment is not None:
        transcript.update_transcript_segment(
            repository,
            assistant_message=assistant_message,
            segment=tool_call_segment,
            status="running",
            metadata_json={
                **dict(tool_call_metadata),
                "governance_action": action,
                "continuation_token": contract.continuation_token,
                "continuation_resolved": dict(resolution_payload),
            },
        )
    if tool_step is not None:
        repository.update_generation_step(
            tool_step,
            phase="tool_running",
            status="running",
            state="resumed",
            safe_summary=None,
            metadata_json={
                **dict(tool_step.metadata_json),
                "continuation_resolved": dict(resolution_payload),
            },
        )
    await publish_assistant_trace(
        repository,
        event_broker,
        session_id=session.id,
        assistant_message=assistant_message,
        entry={
            "state": "tool.resumed",
            "tool": tool_request.tool_name,
            "tool_call_id": tool_request.tool_call_id,
            "action": action,
            "continuation_token": contract.continuation_token,
        },
    )
    return dict(resolution_payload)
