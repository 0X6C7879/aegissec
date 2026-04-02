from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession

from app.compat.mcp.service import MCPService, get_mcp_service
from app.compat.skills.service import (
    SkillContentReadError,
    SkillLookupError,
    SkillService,
    get_skill_service,
)
from app.core.events import SessionEvent, SessionEventBroker, SessionEventType, get_event_broker
from app.core.settings import get_settings
from app.db.models import (
    AssistantTranscriptSegment,
    AssistantTranscriptSegmentKind,
    BranchForkRequest,
    ChatGeneration,
    ChatGenerationRead,
    ChatRequest,
    ChatResponse,
    GenerationAction,
    GenerationStatus,
    GenerationStep,
    GenerationStepRead,
    Message,
    MessageEditRequest,
    MessageKind,
    MessageMutationResponse,
    MessageRegenerateRequest,
    MessageRole,
    MessageRollbackRequest,
    MessageStatus,
    RuntimeExecuteRequest,
    RuntimePolicy,
    Session,
    SessionConversationRead,
    SessionStatus,
    attachments_from_storage,
    attachments_to_storage,
    to_chat_generation_read,
    to_conversation_branch_read,
    to_generation_step_read,
    to_message_read,
    to_session_read,
    utc_now,
)
from app.db.repositories import SessionRepository
from app.db.session import get_db_session
from app.services.capabilities import CapabilityFacade
from app.services.chat_runtime import (
    ChatRuntime,
    ChatRuntimeConfigurationError,
    ChatRuntimeError,
    ConversationMessage,
    GenerationCallbacks,
    ToolCallRequest,
    ToolCallResult,
    get_chat_runtime,
    sanitize_assistant_content,
    strip_think_blocks,
)
from app.services.runtime import (
    RuntimeArtifactPathError,
    RuntimeOperationError,
    RuntimePolicyViolationError,
    RuntimeService,
    get_runtime_service,
)
from app.services.session_generation import (
    GenerationCancelledError,
    SessionGenerationManager,
    get_generation_manager,
)

router = APIRouter(prefix="/api/sessions", tags=["chat"])

SAFE_THINKING_SUMMARY = "Assistant is analyzing the request and preparing a response."
THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_HIDDEN_STREAM_TAG_NAMES = {"think", "invoke", "tool_call"}
_HIDDEN_STREAM_TAG_NAME_RE = re.compile(
    r"^<\s*(/)?\s*(?:[\w-]+:)?([a-z_]+)",
    re.IGNORECASE,
)
CHAT_EXPOSE_THINKING = get_settings().chat_expose_thinking


def _match_hidden_stream_tag(fragment: str) -> tuple[str, bool, bool, bool] | None:
    match = _HIDDEN_STREAM_TAG_NAME_RE.match(fragment)
    if match is None:
        return None

    tag_name = match.group(2).lower()
    is_closing = bool(match.group(1))
    is_complete = ">" in fragment
    hidden_names = _hidden_stream_tag_names()
    if is_complete:
        if tag_name not in hidden_names:
            return None
    elif not any(hidden_name.startswith(tag_name) for hidden_name in hidden_names):
        return None

    is_self_closing = is_complete and fragment.rstrip().endswith("/>")
    return tag_name, is_closing, is_complete, is_self_closing


def _pop_hidden_stream_tag(hidden_stack: list[str], tag_name: str) -> None:
    for index in range(len(hidden_stack) - 1, -1, -1):
        if hidden_stack[index] == tag_name:
            del hidden_stack[index:]
            return


def _get_session_or_404(repository: SessionRepository, session_id: str) -> Session:
    session = repository.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


def _get_message_or_404(
    repository: SessionRepository,
    *,
    session_id: str,
    message_id: str,
) -> Message:
    message = repository.get_message(message_id)
    if message is None or message.session_id != session_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    return message


def _message_trace_entries(message: Message) -> list[dict[str, object]]:
    raw_trace = message.metadata_json.get("trace")
    if not isinstance(raw_trace, list):
        return []
    return [dict(entry) for entry in raw_trace if isinstance(entry, dict)]


def _hidden_stream_tag_names() -> set[str]:
    if CHAT_EXPOSE_THINKING:
        return {"invoke", "tool_call"}
    return set(_HIDDEN_STREAM_TAG_NAMES)


def _sanitize_persisted_assistant_text(content: str, *, fallback: str = "") -> str:
    return sanitize_assistant_content(
        content,
        strip_thinking=not CHAT_EXPOSE_THINKING,
        fallback_text=fallback,
    )


def _message_transcript_segments(
    repository: SessionRepository, assistant_message: Message
) -> list[AssistantTranscriptSegment]:
    return repository.get_message_transcript(assistant_message)


def _find_transcript_segment(
    segments: list[AssistantTranscriptSegment],
    *,
    kind: AssistantTranscriptSegmentKind | None = None,
    tool_call_id: str | None = None,
) -> AssistantTranscriptSegment | None:
    for segment in reversed(segments):
        if kind is not None and segment.kind != kind:
            continue
        if tool_call_id is not None and segment.tool_call_id != tool_call_id:
            continue
        return segment
    return None


def _latest_transcript_segment(
    segments: list[AssistantTranscriptSegment],
) -> AssistantTranscriptSegment | None:
    if not segments:
        return None
    return max(segments, key=lambda segment: (segment.sequence, segment.recorded_at, segment.id))


def _append_transcript_segment(
    repository: SessionRepository,
    *,
    assistant_message: Message,
    kind: AssistantTranscriptSegmentKind,
    status: str | None = None,
    title: str | None = None,
    text: str | None = None,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    metadata_json: dict[str, object] | None = None,
) -> AssistantTranscriptSegment:
    segments = _message_transcript_segments(repository, assistant_message)
    next_sequence = max((segment.sequence for segment in segments), default=0) + 1
    now = utc_now()
    segment = AssistantTranscriptSegment(
        id=str(uuid4()),
        sequence=next_sequence,
        kind=kind,
        status=status,
        title=title,
        text=text,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        recorded_at=now,
        updated_at=now,
        metadata=metadata_json or {},
    )
    repository.append_message_transcript_segment(assistant_message, segment)
    return segment


def _update_transcript_segment(
    repository: SessionRepository,
    *,
    assistant_message: Message,
    segment: AssistantTranscriptSegment,
    status: str | None = None,
    title: str | None = None,
    text: str | None = None,
    metadata_json: dict[str, object] | None = None,
) -> AssistantTranscriptSegment:
    merged_metadata = dict(segment.metadata_payload)
    if metadata_json is not None:
        merged_metadata.update(metadata_json)
    updated_segment = segment.model_copy(
        update={
            "status": status if status is not None else segment.status,
            "title": title if title is not None else segment.title,
            "text": text if text is not None else segment.text,
            "updated_at": utc_now(),
            "metadata_payload": merged_metadata,
        }
    )
    repository.update_message_transcript_segment(assistant_message, updated_segment)
    return updated_segment


def _append_output_transcript_delta(
    repository: SessionRepository,
    *,
    assistant_message: Message,
    delta_text: str,
    status: str,
    append_to_current: bool,
) -> AssistantTranscriptSegment | None:
    transcript_segments = _message_transcript_segments(repository, assistant_message)
    latest_segment = _latest_transcript_segment(transcript_segments)
    if append_to_current and latest_segment is not None:
        if latest_segment.kind != AssistantTranscriptSegmentKind.OUTPUT:
            if not delta_text:
                return None
            return _append_transcript_segment(
                repository,
                assistant_message=assistant_message,
                kind=AssistantTranscriptSegmentKind.OUTPUT,
                status=status,
                title=None,
                text=delta_text,
            )

        next_text = (
            f"{latest_segment.text or ''}{delta_text}" if delta_text else latest_segment.text
        )
        return _update_transcript_segment(
            repository,
            assistant_message=assistant_message,
            segment=latest_segment,
            status=status,
            text=next_text,
        )

    if not delta_text:
        if (
            latest_segment is not None
            and latest_segment.kind == AssistantTranscriptSegmentKind.OUTPUT
        ):
            return _update_transcript_segment(
                repository,
                assistant_message=assistant_message,
                segment=latest_segment,
                status=status,
            )
        return None

    return _append_transcript_segment(
        repository,
        assistant_message=assistant_message,
        kind=AssistantTranscriptSegmentKind.OUTPUT,
        status=status,
        title=None,
        text=delta_text,
    )


def _persist_reasoning_trace_entry(
    repository: SessionRepository,
    *,
    assistant_message: Message,
    entry: dict[str, object],
) -> dict[str, object]:
    generation = None
    generation_trace_length = 0
    if assistant_message.generation_id is not None:
        generation = repository.get_generation(assistant_message.generation_id)
        if generation is not None:
            generation_trace_length = len(generation.reasoning_trace_json)

    message_trace_length = len(_message_trace_entries(assistant_message))
    persisted_entry = {
        "sequence": max(message_trace_length, generation_trace_length) + 1,
        "recorded_at": utc_now().isoformat(),
        **entry,
    }

    repository.append_message_trace(assistant_message, persisted_entry)
    if generation is not None:
        generation_trace = list(generation.reasoning_trace_json)
        generation_trace.append(dict(persisted_entry))
        repository.update_generation(generation, reasoning_trace_json=generation_trace)
    return persisted_entry


async def _publish_session_updated(
    event_broker: SessionEventBroker,
    session: Session,
    *,
    error: str | None = None,
    queued_prompt_count: int | None = None,
) -> None:
    payload: dict[str, Any] = to_session_read(session).model_dump(mode="json")
    if error is not None:
        payload["error"] = error
    if queued_prompt_count is not None:
        payload["queued_prompt_count"] = queued_prompt_count
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_UPDATED,
            session_id=session.id,
            payload=payload,
        )
    )


def _message_payload(message: Message) -> dict[str, Any]:
    payload = to_message_read(message).model_dump(mode="json")
    payload["message_id"] = payload["id"]
    return payload


async def _publish_message_event(
    event_broker: SessionEventBroker,
    *,
    event_type: SessionEventType,
    session_id: str,
    message: Message,
    delta: str | None = None,
) -> None:
    payload = _message_payload(message)
    if delta is not None:
        payload["delta"] = delta
    await event_broker.publish(
        SessionEvent(type=event_type, session_id=session_id, payload=payload)
    )


async def _publish_generation_started(
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    generation_id: str,
    user_message_id: str | None,
    assistant_message_id: str,
    queued_prompt_count: int,
) -> None:
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.GENERATION_STARTED,
            session_id=session_id,
            payload={
                "generation_id": generation_id,
                "user_message_id": user_message_id,
                "message_id": assistant_message_id,
                "queued_prompt_count": queued_prompt_count,
            },
        )
    )


async def _publish_generation_cancelled(
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    generation_id: str,
    assistant_message_id: str,
) -> None:
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.GENERATION_CANCELLED,
            session_id=session_id,
            payload={"generation_id": generation_id, "message_id": assistant_message_id},
        )
    )


async def _publish_generation_failed(
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    generation_id: str,
    assistant_message_id: str,
    error_message: str,
) -> None:
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.GENERATION_FAILED,
            session_id=session_id,
            payload={
                "generation_id": generation_id,
                "message_id": assistant_message_id,
                "error": error_message,
            },
        )
    )


async def _publish_assistant_summary(
    repository: SessionRepository,
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    assistant_message: Message,
    summary: str,
) -> None:
    sanitized_summary = _sanitize_persisted_assistant_text(summary)
    if not sanitized_summary:
        sanitized_summary = SAFE_THINKING_SUMMARY
    repository.update_message_summary(assistant_message, sanitized_summary)
    transcript_segments = _message_transcript_segments(repository, assistant_message)
    reasoning_segment = _find_transcript_segment(
        transcript_segments, kind=AssistantTranscriptSegmentKind.REASONING
    )
    if reasoning_segment is None:
        _append_transcript_segment(
            repository,
            assistant_message=assistant_message,
            kind=AssistantTranscriptSegmentKind.REASONING,
            status="completed",
            title=None,
            text=sanitized_summary,
            metadata_json={"event": SessionEventType.ASSISTANT_SUMMARY.value},
        )
    else:
        _update_transcript_segment(
            repository,
            assistant_message=assistant_message,
            segment=reasoning_segment,
            status="completed",
            title=None,
            text=sanitized_summary,
            metadata_json={"event": SessionEventType.ASSISTANT_SUMMARY.value},
        )
    if assistant_message.generation_id is not None:
        generation = repository.get_generation(assistant_message.generation_id)
        if generation is not None:
            repository.update_generation(generation, reasoning_summary=sanitized_summary)
    _record_generation_step(
        repository,
        assistant_message=assistant_message,
        kind="reasoning",
        phase="planning",
        status="completed",
        state="summary.updated",
        label="推理摘要",
        safe_summary=sanitized_summary,
        metadata_json={"event": SessionEventType.ASSISTANT_SUMMARY.value},
    )
    _persist_reasoning_trace_entry(
        repository,
        assistant_message=assistant_message,
        entry={
            "event": SessionEventType.ASSISTANT_SUMMARY.value,
            "state": "summary.updated",
            "summary": sanitized_summary,
        },
    )
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.ASSISTANT_SUMMARY,
            session_id=session_id,
            payload={"message_id": assistant_message.id, "summary": sanitized_summary},
        )
    )
    await _publish_message_event(
        event_broker,
        event_type=SessionEventType.MESSAGE_UPDATED,
        session_id=session_id,
        message=assistant_message,
    )


async def _publish_assistant_trace(
    repository: SessionRepository,
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    assistant_message: Message,
    entry: dict[str, object],
) -> None:
    sanitized_entry: dict[str, object] = {}
    for key, value in entry.items():
        if isinstance(value, str):
            sanitized_value = _sanitize_persisted_assistant_text(value)
            if THINK_BLOCK_RE.search(value) and not sanitized_value:
                continue
            sanitized_entry[key] = sanitized_value
        else:
            sanitized_entry[key] = value

    persisted_entry = _persist_reasoning_trace_entry(
        repository,
        assistant_message=assistant_message,
        entry={"event": SessionEventType.ASSISTANT_TRACE.value, **sanitized_entry},
    )
    _record_generation_step(
        repository,
        assistant_message=assistant_message,
        kind="status",
        phase=_infer_trace_phase(sanitized_entry),
        status=_infer_trace_status(sanitized_entry),
        state=(
            str(sanitized_entry.get("state"))
            if sanitized_entry.get("state") is not None
            else "trace"
        ),
        label="过程更新",
        safe_summary=_infer_trace_summary(sanitized_entry),
        metadata_json={key: value for key, value in persisted_entry.items() if key != "summary"},
    )
    trace_state = str(sanitized_entry.get("state") or "")
    if trace_state.startswith("generation."):
        transcript_kind = (
            AssistantTranscriptSegmentKind.ERROR
            if trace_state == "generation.failed"
            else AssistantTranscriptSegmentKind.STATUS
        )
        _append_transcript_segment(
            repository,
            assistant_message=assistant_message,
            kind=transcript_kind,
            status=_infer_trace_status(sanitized_entry),
            title=None,
            text=_infer_trace_summary(sanitized_entry),
            metadata_json={key: value for key, value in persisted_entry.items()},
        )
    payload = {"message_id": assistant_message.id, **persisted_entry}
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.ASSISTANT_TRACE,
            session_id=session_id,
            payload=payload,
        )
    )
    if trace_state.startswith("generation."):
        await _publish_message_event(
            event_broker,
            event_type=SessionEventType.MESSAGE_UPDATED,
            session_id=session_id,
            message=assistant_message,
        )


def _project_visible_stream_content(content: str) -> str:
    visible_parts: list[str] = []
    index = 0
    hidden_stack: list[str] = []

    while index < len(content):
        if content[index] == "<":
            tag_end = content.find(">", index)
            next_index = index + 1
            if tag_end == -1 and next_index < len(content):
                next_char = content[next_index]
                if next_char in {"/", "!", "?"} or next_char.isalpha() or next_char == "_":
                    break
            tag_fragment = content[index:] if tag_end == -1 else content[index : tag_end + 1]
            hidden_tag = _match_hidden_stream_tag(tag_fragment)

            if hidden_tag is not None:
                tag_name, is_closing, is_complete, is_self_closing = hidden_tag
                if not is_complete:
                    break

                if is_closing:
                    _pop_hidden_stream_tag(hidden_stack, tag_name)
                elif not is_self_closing:
                    hidden_stack.append(tag_name)

                index += len(tag_fragment)
                continue

            if hidden_stack:
                if tag_end == -1:
                    break
                index = tag_end + 1
                continue

        if hidden_stack:
            index += 1
            continue

        visible_parts.append(content[index])
        index += 1

    return _sanitize_persisted_assistant_text("".join(visible_parts))


def _build_conversation_messages(messages: list[Message]) -> list[ConversationMessage]:
    conversation_messages: list[ConversationMessage] = []
    for message in messages:
        if message.message_kind != MessageKind.MESSAGE:
            continue
        if message.role not in {MessageRole.USER, MessageRole.ASSISTANT}:
            continue
        if message.role == MessageRole.ASSISTANT and not message.content.strip():
            continue
        conversation_content = message.content
        if message.role == MessageRole.ASSISTANT:
            conversation_content = _sanitize_persisted_assistant_text(message.content)
        conversation_messages.append(
            ConversationMessage(
                role=message.role,
                content=conversation_content,
                attachments=attachments_from_storage(message.attachments_json),
            )
        )
    return conversation_messages


def _build_conversation_read(
    repository: SessionRepository,
    session: Session,
) -> SessionConversationRead:
    active_branch = repository.ensure_active_branch(session)
    branches = repository.list_branches(session.id)
    messages = repository.list_messages(
        session.id, branch_id=active_branch.id, include_superseded=False
    )
    generations = [
        generation
        for generation in repository.list_generations(session.id)
        if generation.branch_id == active_branch.id
    ]
    active_generation = repository.get_active_generation(session.id)
    return SessionConversationRead(
        session=to_session_read(session),
        active_branch=to_conversation_branch_read(active_branch),
        branches=[to_conversation_branch_read(branch) for branch in branches],
        messages=[to_message_read(message) for message in messages],
        generations=_build_generation_reads(repository, session.id, generations),
        active_generation_id=active_generation.id if active_generation is not None else None,
        queued_generation_count=repository.queue_size(session.id),
    )


def _build_generation_reads(
    repository: SessionRepository,
    session_id: str,
    generations: list[ChatGeneration],
) -> list[ChatGenerationRead]:
    generation_ids = [generation.id for generation in generations]
    steps_by_generation_id: dict[str, list[GenerationStepRead]] = {}
    for step in repository.list_generation_steps(generation_ids=generation_ids):
        steps_by_generation_id.setdefault(step.generation_id, []).append(
            to_generation_step_read(step)
        )

    queue_positions = {
        generation.id: index
        for index, generation in enumerate(
            repository.list_generations(session_id, statuses={GenerationStatus.QUEUED}),
            start=1,
        )
    }

    reads: list[ChatGenerationRead] = []
    for generation in generations:
        generation_read = to_chat_generation_read(generation)
        generation_read.steps = list(steps_by_generation_id.get(generation.id, []))
        generation_read.queue_position = queue_positions.get(generation.id)
        reads.append(generation_read)
    return reads


def _build_generation_read(
    repository: SessionRepository,
    generation: ChatGeneration,
) -> ChatGenerationRead:
    return _build_generation_reads(repository, generation.session_id, [generation])[0]


def _build_queue_metadata(
    repository: SessionRepository,
    session_id: str,
    generation_id: str | None = None,
) -> tuple[str | None, int | None, int]:
    active_generation = repository.get_active_generation(session_id)
    queued_generation_count = repository.queue_size(session_id)
    queue_position = (
        repository.get_generation_queue_position(session_id, generation_id)
        if generation_id is not None
        else None
    )
    return (
        active_generation.id if active_generation is not None else None,
        queue_position,
        queued_generation_count,
    )


def _record_generation_step(
    repository: SessionRepository,
    *,
    assistant_message: Message,
    kind: str,
    phase: str | None = None,
    status: str,
    state: str | None = None,
    label: str | None = None,
    safe_summary: str | None = None,
    delta_text: str = "",
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    command: str | None = None,
    metadata_json: dict[str, object] | None = None,
    ended_at: datetime | None = None,
) -> None:
    if assistant_message.generation_id is None:
        return
    repository.create_generation_step(
        generation_id=assistant_message.generation_id,
        session_id=assistant_message.session_id,
        message_id=assistant_message.id,
        kind=kind,
        phase=phase,
        status=status,
        state=state,
        label=label,
        safe_summary=safe_summary,
        delta_text=delta_text,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        command=command,
        metadata_json=metadata_json,
        ended_at=ended_at,
    )


def _get_or_create_output_step(
    repository: SessionRepository,
    *,
    assistant_message: Message,
) -> GenerationStep | None:
    if assistant_message.generation_id is None:
        return None
    output_step = repository.get_open_generation_step(
        assistant_message.generation_id, kind="output"
    )
    if output_step is not None:
        return output_step
    return repository.create_generation_step(
        generation_id=assistant_message.generation_id,
        session_id=assistant_message.session_id,
        message_id=assistant_message.id,
        kind="output",
        phase="synthesis",
        status="running",
        state="streaming",
        label="正文输出",
    )


def _infer_trace_phase(entry: dict[str, object]) -> str:
    state = str(entry.get("state") or "")
    if state in {"generation.completed", "summary.updated"}:
        return "completed" if state == "generation.completed" else "planning"
    if state == "generation.cancelled":
        return "cancelled"
    if state == "generation.failed":
        return "failed"
    if state == "generation.started":
        return "planning"
    if state == "tool.started":
        return "tool_running"
    if state in {"tool.finished", "tool.failed"}:
        return "tool_result"
    return "planning"


def _infer_trace_status(entry: dict[str, object]) -> str:
    state = str(entry.get("state") or "")
    if state in {"generation.completed", "tool.finished", "summary.updated"}:
        return "completed"
    if state in {"generation.failed", "tool.failed"}:
        return "failed"
    if state == "generation.cancelled":
        return "cancelled"
    if state in {"generation.started", "tool.started"}:
        return "running"
    return "completed"


def _infer_trace_summary(entry: dict[str, object]) -> str | None:
    summary = entry.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    state = str(entry.get("state") or "")
    tool_name = entry.get("tool")
    tool_display = str(tool_name) if isinstance(tool_name, str) and tool_name else "tool"
    if state == "generation.started":
        return "Generation started."
    if state == "generation.completed":
        return "Generation completed."
    if state == "generation.cancelled":
        return "Generation cancelled."
    if state == "generation.failed":
        error_value = entry.get("error")
        return (
            str(error_value)
            if isinstance(error_value, str) and error_value
            else "Generation failed."
        )
    if state == "tool.started":
        return f"Running {tool_display}."
    if state == "tool.finished":
        return f"Completed {tool_display}."
    if state == "tool.failed":
        error_value = entry.get("error")
        if isinstance(error_value, str) and error_value:
            return error_value
        return f"{tool_display} failed."
    return None


def _find_sibling_version_group_id(
    repository: SessionRepository,
    *,
    session_id: str,
    branch_id: str | None,
    sequence: int,
    role: MessageRole,
) -> str | None:
    for message in repository.list_all_messages(session_id):
        if (
            message.branch_id == branch_id
            and message.sequence == sequence
            and message.role == role
            and message.version_group_id is not None
        ):
            return message.version_group_id
    return None


def _build_tool_executor(
    *,
    session: Session,
    assistant_message: Message,
    repository: SessionRepository,
    event_broker: SessionEventBroker,
    runtime_service: RuntimeService,
    skill_service: SkillService,
    mcp_service: MCPService,
) -> Any:
    available_skills = skill_service.list_loaded_skills_for_agent()
    del mcp_service

    async def execute_tool(tool_request: ToolCallRequest) -> ToolCallResult:
        started_payload: dict[str, Any] = {
            "tool": tool_request.tool_name,
            "tool_call_id": tool_request.tool_call_id,
            "arguments": tool_request.arguments,
        }
        if tool_request.tool_name == "execute_kali_command":
            started_payload.update(
                {
                    "command": tool_request.arguments.get("command"),
                    "timeout_seconds": tool_request.arguments.get("timeout_seconds"),
                    "artifact_paths": tool_request.arguments.get("artifact_paths", []),
                }
            )

        await _publish_assistant_trace(
            repository,
            event_broker,
            session_id=session.id,
            assistant_message=assistant_message,
            entry={
                "state": "tool.started",
                "tool": tool_request.tool_name,
                "tool_call_id": tool_request.tool_call_id,
            },
        )
        _append_transcript_segment(
            repository,
            assistant_message=assistant_message,
            kind=AssistantTranscriptSegmentKind.TOOL_CALL,
            status="running",
            title=tool_request.tool_name,
            text=(
                str(tool_request.arguments.get("command"))
                if isinstance(tool_request.arguments.get("command"), str)
                else None
            ),
            tool_name=tool_request.tool_name,
            tool_call_id=tool_request.tool_call_id,
            metadata_json={"arguments": dict(tool_request.arguments)},
        )
        await _publish_message_event(
            event_broker,
            event_type=SessionEventType.MESSAGE_UPDATED,
            session_id=session.id,
            message=assistant_message,
        )
        await event_broker.publish(
            SessionEvent(
                type=SessionEventType.TOOL_CALL_STARTED,
                session_id=session.id,
                payload=started_payload,
            )
        )
        if assistant_message.generation_id is not None:
            repository.create_generation_step(
                generation_id=assistant_message.generation_id,
                session_id=session.id,
                message_id=assistant_message.id,
                kind="tool",
                phase="tool_running",
                status="running",
                state="started",
                label=tool_request.tool_name,
                tool_name=tool_request.tool_name,
                tool_call_id=tool_request.tool_call_id,
                command=(
                    str(tool_request.arguments.get("command"))
                    if isinstance(tool_request.arguments.get("command"), str)
                    else None
                ),
                metadata_json={"arguments": dict(tool_request.arguments)},
            )

        async def publish_tool_failed(error_message: str) -> None:
            if assistant_message.generation_id is not None:
                tool_step = repository.get_open_generation_step(
                    assistant_message.generation_id,
                    kind="tool",
                    tool_call_id=tool_request.tool_call_id,
                )
                if tool_step is not None:
                    repository.update_generation_step(
                        tool_step,
                        phase="tool_result",
                        status="failed",
                        state="failed",
                        safe_summary=error_message,
                        ended_at=utc_now(),
                        metadata_json={**dict(tool_step.metadata_json), "error": error_message},
                    )
            transcript_segments = _message_transcript_segments(repository, assistant_message)
            tool_call_segment = _find_transcript_segment(
                transcript_segments,
                kind=AssistantTranscriptSegmentKind.TOOL_CALL,
                tool_call_id=tool_request.tool_call_id,
            )
            if tool_call_segment is not None:
                _update_transcript_segment(
                    repository,
                    assistant_message=assistant_message,
                    segment=tool_call_segment,
                    status="failed",
                    metadata_json={"error": error_message},
                )
            _append_transcript_segment(
                repository,
                assistant_message=assistant_message,
                kind=AssistantTranscriptSegmentKind.ERROR,
                status="failed",
                title=tool_request.tool_name,
                text=error_message,
                tool_name=tool_request.tool_name,
                tool_call_id=tool_request.tool_call_id,
                metadata_json={"arguments": dict(tool_request.arguments), "error": error_message},
            )
            await _publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=assistant_message,
                entry={
                    "state": "tool.failed",
                    "tool": tool_request.tool_name,
                    "tool_call_id": tool_request.tool_call_id,
                    "error": error_message,
                },
            )
            await event_broker.publish(
                SessionEvent(
                    type=SessionEventType.TOOL_CALL_FAILED,
                    session_id=session.id,
                    payload={**started_payload, "error": error_message},
                )
            )
            await _publish_message_event(
                event_broker,
                event_type=SessionEventType.MESSAGE_UPDATED,
                session_id=session.id,
                message=assistant_message,
            )

        if tool_request.tool_name == "execute_kali_command":
            command = tool_request.arguments.get("command")
            timeout_seconds = tool_request.arguments.get("timeout_seconds")
            artifact_paths = tool_request.arguments.get("artifact_paths", [])
            if not isinstance(command, str) or not command.strip():
                await publish_tool_failed("Invalid command for execute_kali_command.")
                raise ChatRuntimeError("Invalid command for execute_kali_command.")
            if timeout_seconds is not None and not isinstance(timeout_seconds, int):
                await publish_tool_failed("Invalid timeout for execute_kali_command.")
                raise ChatRuntimeError("Invalid timeout for execute_kali_command.")
            if not isinstance(artifact_paths, list) or not all(
                isinstance(item, str) for item in artifact_paths
            ):
                await publish_tool_failed("Invalid artifact paths for execute_kali_command.")
                raise ChatRuntimeError("Invalid artifact paths for execute_kali_command.")

            try:
                run = runtime_service.execute(
                    RuntimeExecuteRequest(
                        command=command,
                        timeout_seconds=timeout_seconds,
                        session_id=session.id,
                        artifact_paths=artifact_paths,
                    ),
                    runtime_policy=RuntimePolicy.model_validate(session.runtime_policy_json or {}),
                )
            except ValidationError as exc:
                await publish_tool_failed(f"Invalid runtime policy: {exc.errors()[0]['msg']}")
                raise ChatRuntimeError("Invalid runtime policy for this session.") from exc
            except (
                RuntimeArtifactPathError,
                RuntimeOperationError,
                RuntimePolicyViolationError,
            ) as exc:
                await publish_tool_failed(str(exc))
                raise ChatRuntimeError(str(exc)) from exc

            command_result_payload: dict[str, Any] = {
                "status": run.status.value,
                "exit_code": run.exit_code,
                "stdout": run.stdout,
                "stderr": run.stderr,
                "artifacts": [artifact.relative_path for artifact in run.artifacts],
            }
            transcript_segments = _message_transcript_segments(repository, assistant_message)
            tool_call_segment = _find_transcript_segment(
                transcript_segments,
                kind=AssistantTranscriptSegmentKind.TOOL_CALL,
                tool_call_id=tool_request.tool_call_id,
            )
            if tool_call_segment is not None:
                _update_transcript_segment(
                    repository,
                    assistant_message=assistant_message,
                    segment=tool_call_segment,
                    status="completed",
                    metadata_json={"status": run.status.value, "run_id": run.id},
                )
            _append_transcript_segment(
                repository,
                assistant_message=assistant_message,
                kind=AssistantTranscriptSegmentKind.TOOL_RESULT,
                status=run.status.value,
                title=tool_request.tool_name,
                text=f"命令已完成，状态：{run.status.value}。",
                tool_name=tool_request.tool_name,
                tool_call_id=tool_request.tool_call_id,
                metadata_json={
                    "arguments": dict(tool_request.arguments),
                    "result": command_result_payload,
                    "run_id": run.id,
                    "command": run.command,
                    "stdout": run.stdout,
                    "stderr": run.stderr,
                    "artifacts": [artifact.relative_path for artifact in run.artifacts],
                },
            )
            await _publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=assistant_message,
                entry={
                    "state": "tool.finished",
                    "tool": tool_request.tool_name,
                    "tool_call_id": tool_request.tool_call_id,
                    "status": run.status.value,
                },
            )
            await event_broker.publish(
                SessionEvent(
                    type=SessionEventType.TOOL_CALL_FINISHED,
                    session_id=session.id,
                    payload={
                        **started_payload,
                        "tool": tool_request.tool_name,
                        "tool_call_id": tool_request.tool_call_id,
                        "run_id": run.id,
                        "command": run.command,
                        "status": run.status.value,
                        "exit_code": run.exit_code,
                        "requested_timeout_seconds": run.requested_timeout_seconds,
                        "stdout": run.stdout,
                        "stderr": run.stderr,
                        "created_at": run.created_at.isoformat(),
                        "artifact_paths": [artifact.relative_path for artifact in run.artifacts],
                        "result": command_result_payload,
                    },
                )
            )
            await _publish_message_event(
                event_broker,
                event_type=SessionEventType.MESSAGE_UPDATED,
                session_id=session.id,
                message=assistant_message,
            )
            if assistant_message.generation_id is not None:
                tool_step = repository.get_open_generation_step(
                    assistant_message.generation_id,
                    kind="tool",
                    tool_call_id=tool_request.tool_call_id,
                )
                if tool_step is not None:
                    repository.update_generation_step(
                        tool_step,
                        phase="tool_result",
                        status="completed",
                        state="finished",
                        safe_summary=f"命令已完成，状态：{run.status.value}。",
                        ended_at=utc_now(),
                        metadata_json={
                            **dict(tool_step.metadata_json),
                            "result": command_result_payload,
                            "run_id": run.id,
                            "status": run.status.value,
                        },
                    )
            return ToolCallResult(tool_name=tool_request.tool_name, payload=command_result_payload)

        if tool_request.tool_name == "list_available_skills":
            skills_result_payload: dict[str, Any] = {
                "skills": [skill.model_dump(mode="json") for skill in available_skills],
            }
            transcript_segments = _message_transcript_segments(repository, assistant_message)
            tool_call_segment = _find_transcript_segment(
                transcript_segments,
                kind=AssistantTranscriptSegmentKind.TOOL_CALL,
                tool_call_id=tool_request.tool_call_id,
            )
            if tool_call_segment is not None:
                _update_transcript_segment(
                    repository,
                    assistant_message=assistant_message,
                    segment=tool_call_segment,
                    status="completed",
                )
            _append_transcript_segment(
                repository,
                assistant_message=assistant_message,
                kind=AssistantTranscriptSegmentKind.TOOL_RESULT,
                status="completed",
                title=tool_request.tool_name,
                text="已列出当前可用技能。",
                tool_name=tool_request.tool_name,
                tool_call_id=tool_request.tool_call_id,
                metadata_json={"result": skills_result_payload},
            )
            await _publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=assistant_message,
                entry={
                    "state": "tool.finished",
                    "tool": tool_request.tool_name,
                    "tool_call_id": tool_request.tool_call_id,
                },
            )
            await event_broker.publish(
                SessionEvent(
                    type=SessionEventType.TOOL_CALL_FINISHED,
                    session_id=session.id,
                    payload={**started_payload, "result": skills_result_payload},
                )
            )
            await _publish_message_event(
                event_broker,
                event_type=SessionEventType.MESSAGE_UPDATED,
                session_id=session.id,
                message=assistant_message,
            )
            if assistant_message.generation_id is not None:
                tool_step = repository.get_open_generation_step(
                    assistant_message.generation_id,
                    kind="tool",
                    tool_call_id=tool_request.tool_call_id,
                )
                if tool_step is not None:
                    repository.update_generation_step(
                        tool_step,
                        phase="tool_result",
                        status="completed",
                        state="finished",
                        safe_summary="已列出当前可用技能。",
                        ended_at=utc_now(),
                        metadata_json={
                            **dict(tool_step.metadata_json),
                            "result": skills_result_payload,
                        },
                    )
            return ToolCallResult(tool_name=tool_request.tool_name, payload=skills_result_payload)

        if tool_request.tool_name == "read_skill_content":
            skill_name_or_id = tool_request.arguments.get("skill_name_or_id")
            if not isinstance(skill_name_or_id, str) or not skill_name_or_id.strip():
                await publish_tool_failed("read_skill_content requires a valid skill identifier.")
                raise ChatRuntimeError("read_skill_content requires a valid skill identifier.")

            try:
                skill_content = skill_service.read_skill_content_by_name_or_directory_name(
                    skill_name_or_id
                )
            except (SkillLookupError, SkillContentReadError) as exc:
                await publish_tool_failed(str(exc))
                raise ChatRuntimeError(str(exc)) from exc

            skill_result_payload: dict[str, Any] = {"skill": skill_content.model_dump(mode="json")}
            transcript_segments = _message_transcript_segments(repository, assistant_message)
            tool_call_segment = _find_transcript_segment(
                transcript_segments,
                kind=AssistantTranscriptSegmentKind.TOOL_CALL,
                tool_call_id=tool_request.tool_call_id,
            )
            if tool_call_segment is not None:
                _update_transcript_segment(
                    repository,
                    assistant_message=assistant_message,
                    segment=tool_call_segment,
                    status="completed",
                )
            _append_transcript_segment(
                repository,
                assistant_message=assistant_message,
                kind=AssistantTranscriptSegmentKind.TOOL_RESULT,
                status="completed",
                title=tool_request.tool_name,
                text=f"已读取 {skill_name_or_id} 的技能内容。",
                tool_name=tool_request.tool_name,
                tool_call_id=tool_request.tool_call_id,
                metadata_json={"result": skill_result_payload},
            )
            await _publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=assistant_message,
                entry={
                    "state": "tool.finished",
                    "tool": tool_request.tool_name,
                    "tool_call_id": tool_request.tool_call_id,
                },
            )
            await event_broker.publish(
                SessionEvent(
                    type=SessionEventType.TOOL_CALL_FINISHED,
                    session_id=session.id,
                    payload={**started_payload, "result": skill_result_payload},
                )
            )
            await _publish_message_event(
                event_broker,
                event_type=SessionEventType.MESSAGE_UPDATED,
                session_id=session.id,
                message=assistant_message,
            )
            if assistant_message.generation_id is not None:
                tool_step = repository.get_open_generation_step(
                    assistant_message.generation_id,
                    kind="tool",
                    tool_call_id=tool_request.tool_call_id,
                )
                if tool_step is not None:
                    repository.update_generation_step(
                        tool_step,
                        phase="tool_result",
                        status="completed",
                        state="finished",
                        safe_summary=f"已读取 {skill_name_or_id} 的技能内容。",
                        ended_at=utc_now(),
                        metadata_json={
                            **dict(tool_step.metadata_json),
                            "result": skill_result_payload,
                        },
                    )
            return ToolCallResult(tool_name=tool_request.tool_name, payload=skill_result_payload)

        error_message = f"Unsupported tool requested: {tool_request.tool_name}."
        await publish_tool_failed(error_message)
        raise ChatRuntimeError(error_message)

    return execute_tool


async def _mark_queued_generations_failed(
    db_engine: Engine,
    *,
    session_id: str,
    error_message: str,
) -> None:
    with DBSession(db_engine) as worker_db_session:
        repository = SessionRepository(worker_db_session)
        queued_generations = repository.list_generations(
            session_id,
            statuses={GenerationStatus.QUEUED},
        )
        for generation in queued_generations:
            repository.mark_generation_failed(generation, error_message)
            assistant_message = repository.get_message(generation.assistant_message_id)
            if assistant_message is not None:
                repository.update_message(
                    assistant_message,
                    status=MessageStatus.FAILED,
                    error_message=error_message,
                )
                _record_generation_step(
                    repository,
                    assistant_message=assistant_message,
                    kind="status",
                    phase="failed",
                    status="failed",
                    state="failed",
                    label="Generation failed",
                    safe_summary=error_message,
                    ended_at=utc_now(),
                    metadata_json={"generation_id": generation.id, "error": error_message},
                )
            repository.close_open_generation_steps(generation.id, status="failed", state="failed")


async def _process_generation(
    *,
    db_engine: Engine,
    session_id: str,
    generation_id: str,
    event_broker: SessionEventBroker,
    generation_manager: SessionGenerationManager,
    chat_runtime: ChatRuntime,
    runtime_service: RuntimeService,
    skill_service: SkillService,
    mcp_service: MCPService,
) -> str:
    with DBSession(db_engine) as worker_db_session:
        repository = SessionRepository(worker_db_session)
        generation = repository.get_generation(generation_id)
        if generation is None:
            raise ChatRuntimeError("Generation record was not found.")
        initial_assistant_message = repository.get_message(generation.assistant_message_id)
        if initial_assistant_message is None:
            raise ChatRuntimeError("Assistant message placeholder was not found.")

    await generation_manager.begin_generation(
        session_id,
        generation_id=generation_id,
        assistant_message_id=initial_assistant_message.id,
    )
    cancel_event = await generation_manager.get_cancel_event(session_id)

    try:
        with DBSession(db_engine) as worker_db_session:
            repository = SessionRepository(worker_db_session)
            session = _get_session_or_404(repository, session_id)
            generation = repository.get_generation(generation_id)
            assistant_message = repository.get_message(initial_assistant_message.id)
            if generation is None or assistant_message is None:
                raise ChatRuntimeError("Generation state could not be reloaded.")
            loaded_assistant_message = assistant_message
            user_message = (
                repository.get_message(generation.user_message_id)
                if generation.user_message_id is not None
                else None
            )
            token_budget = generation.metadata_json.get("token_budget")

            available_skills = skill_service.list_loaded_skills_for_agent()
            capability_facade = CapabilityFacade(
                skill_service=skill_service,
                mcp_service=mcp_service,
            )
            skill_context_prompt = capability_facade.build_skill_prompt_fragment()
            execute_tool = _build_tool_executor(
                session=session,
                assistant_message=loaded_assistant_message,
                repository=repository,
                event_broker=event_broker,
                runtime_service=runtime_service,
                skill_service=skill_service,
                mcp_service=mcp_service,
            )
            conversation_history = repository.build_conversation_context(
                session_id=session.id,
                branch_id=generation.branch_id,
                rough_token_budget=token_budget if isinstance(token_budget, int) else 12_000,
            )

            await _publish_generation_started(
                event_broker,
                session_id=session.id,
                generation_id=generation.id,
                user_message_id=generation.user_message_id,
                assistant_message_id=assistant_message.id,
                queued_prompt_count=repository.queue_size(session.id),
            )
            _record_generation_step(
                repository,
                assistant_message=assistant_message,
                kind="status",
                phase="planning",
                status="running",
                state="started",
                label="Generation started",
                safe_summary="Generation started.",
                metadata_json={"generation_id": generation.id},
            )
            await _publish_assistant_summary(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=assistant_message,
                summary=SAFE_THINKING_SUMMARY,
            )
            await _publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=assistant_message,
                entry={"state": "generation.started", "generation_id": generation.id},
            )

            raw_streamed_content = loaded_assistant_message.content
            streamed_content = _project_visible_stream_content(raw_streamed_content)

            async def on_text_delta(delta: str) -> None:
                nonlocal raw_streamed_content, streamed_content
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                raw_streamed_content += delta
                next_streamed_content = _project_visible_stream_content(raw_streamed_content)
                if next_streamed_content == streamed_content:
                    return

                sanitized_delta = (
                    next_streamed_content[len(streamed_content) :]
                    if next_streamed_content.startswith(streamed_content)
                    else next_streamed_content
                )
                is_incremental_output = next_streamed_content.startswith(streamed_content)
                streamed_content = next_streamed_content
                repository.update_message(
                    loaded_assistant_message,
                    content=streamed_content,
                    status=MessageStatus.STREAMING,
                )
                _append_output_transcript_delta(
                    repository,
                    assistant_message=loaded_assistant_message,
                    delta_text=sanitized_delta,
                    status="running",
                    append_to_current=is_incremental_output,
                )
                output_step = _get_or_create_output_step(
                    repository,
                    assistant_message=loaded_assistant_message,
                )
                if output_step is not None:
                    repository.append_generation_step_delta(output_step, sanitized_delta)
                await _publish_message_event(
                    event_broker,
                    event_type=SessionEventType.MESSAGE_DELTA,
                    session_id=session.id,
                    message=loaded_assistant_message,
                    delta=sanitized_delta,
                )
                await _publish_message_event(
                    event_broker,
                    event_type=SessionEventType.MESSAGE_UPDATED,
                    session_id=session.id,
                    message=loaded_assistant_message,
                )

            async def on_summary(summary: str) -> None:
                if summary.strip():
                    await _publish_assistant_summary(
                        repository,
                        event_broker,
                        session_id=session.id,
                        assistant_message=loaded_assistant_message,
                        summary=summary.strip(),
                    )

            final_content = await chat_runtime.generate_reply(
                (
                    user_message.content
                    if user_message is not None
                    else loaded_assistant_message.content
                ),
                (
                    attachments_from_storage(user_message.attachments_json)
                    if user_message is not None
                    else []
                ),
                conversation_messages=_build_conversation_messages(conversation_history),
                available_skills=available_skills,
                skill_context_prompt=skill_context_prompt,
                execute_tool=execute_tool,
                callbacks=GenerationCallbacks(
                    on_text_delta=on_text_delta,
                    on_summary=on_summary,
                    is_cancelled=cancel_event.is_set,
                ),
            )
            final_content = _sanitize_persisted_assistant_text(final_content) or (
                "模型已完成分析，但没有返回可展示的最终答复。"
            )

            if cancel_event.is_set():
                raise asyncio.CancelledError

            repository.update_message(
                loaded_assistant_message,
                content=final_content,
                status=MessageStatus.COMPLETED,
                error_message="",
            )
            final_delta = (
                final_content[len(streamed_content) :]
                if final_content.startswith(streamed_content)
                else final_content
            )
            final_is_incremental = final_content.startswith(streamed_content)
            _append_output_transcript_delta(
                repository,
                assistant_message=loaded_assistant_message,
                delta_text=final_delta,
                status="completed",
                append_to_current=final_is_incremental,
            )
            repository.mark_generation_completed(generation)
            if final_content != streamed_content:
                if final_delta:
                    await _publish_message_event(
                        event_broker,
                        event_type=SessionEventType.MESSAGE_DELTA,
                        session_id=session.id,
                        message=loaded_assistant_message,
                        delta=final_delta,
                    )
                await _publish_message_event(
                    event_broker,
                    event_type=SessionEventType.MESSAGE_UPDATED,
                    session_id=session.id,
                    message=loaded_assistant_message,
                )
            output_step = _get_or_create_output_step(
                repository,
                assistant_message=loaded_assistant_message,
            )
            if output_step is not None:
                if final_content != output_step.delta_text:
                    repository.update_generation_step(output_step, delta_text=final_content)
                repository.update_generation_step(
                    output_step,
                    phase="synthesis",
                    status="completed",
                    state="completed",
                    ended_at=utc_now(),
                )
            await _publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=loaded_assistant_message,
                entry={"state": "generation.completed", "generation_id": generation.id},
            )
            await _publish_message_event(
                event_broker,
                event_type=SessionEventType.MESSAGE_COMPLETED,
                session_id=session.id,
                message=loaded_assistant_message,
            )
            _record_generation_step(
                repository,
                assistant_message=loaded_assistant_message,
                kind="status",
                phase="completed",
                status="completed",
                state="completed",
                label="Generation completed",
                safe_summary="Generation completed.",
                ended_at=utc_now(),
                metadata_json={"generation_id": generation.id},
            )
            repository.close_open_generation_steps(
                generation.id, status="completed", state="completed"
            )
            return loaded_assistant_message.id
    except asyncio.CancelledError as exc:
        with DBSession(db_engine) as worker_db_session:
            repository = SessionRepository(worker_db_session)
            generation = repository.get_generation(generation_id)
            assistant_message = None
            if generation is not None:
                repository.cancel_generation(
                    generation, error_message="Active generation was cancelled."
                )
                assistant_message = repository.get_message(generation.assistant_message_id)
            if assistant_message is not None:
                repository.update_message(
                    assistant_message,
                    status=MessageStatus.CANCELLED,
                    error_message="Active generation was cancelled.",
                )
                transcript_segments = _message_transcript_segments(repository, assistant_message)
                output_segment = _find_transcript_segment(
                    transcript_segments, kind=AssistantTranscriptSegmentKind.OUTPUT
                )
                if output_segment is not None:
                    _update_transcript_segment(
                        repository,
                        assistant_message=assistant_message,
                        segment=output_segment,
                        status="cancelled",
                    )
                await _publish_assistant_trace(
                    repository,
                    event_broker,
                    session_id=session_id,
                    assistant_message=assistant_message,
                    entry={"state": "generation.cancelled", "generation_id": generation_id},
                )
                _record_generation_step(
                    repository,
                    assistant_message=assistant_message,
                    kind="status",
                    phase="cancelled",
                    status="cancelled",
                    state="cancelled",
                    label="Generation cancelled",
                    safe_summary="Generation cancelled.",
                    ended_at=utc_now(),
                    metadata_json={"generation_id": generation_id},
                )
                repository.close_open_generation_steps(
                    generation_id, status="cancelled", state="cancelled"
                )
                await _publish_generation_cancelled(
                    event_broker,
                    session_id=session_id,
                    generation_id=generation_id,
                    assistant_message_id=assistant_message.id,
                )
        raise GenerationCancelledError("Active generation was cancelled.") from exc
    except (ChatRuntimeConfigurationError, ChatRuntimeError) as exc:
        with DBSession(db_engine) as worker_db_session:
            repository = SessionRepository(worker_db_session)
            generation = repository.get_generation(generation_id)
            assistant_message = None
            if generation is not None:
                repository.mark_generation_failed(generation, str(exc))
                assistant_message = repository.get_message(generation.assistant_message_id)
            if assistant_message is not None:
                repository.update_message(
                    assistant_message,
                    status=MessageStatus.FAILED,
                    error_message=str(exc),
                )
                transcript_segments = _message_transcript_segments(repository, assistant_message)
                output_segment = _find_transcript_segment(
                    transcript_segments, kind=AssistantTranscriptSegmentKind.OUTPUT
                )
                if output_segment is not None:
                    _update_transcript_segment(
                        repository,
                        assistant_message=assistant_message,
                        segment=output_segment,
                        status="failed",
                    )
                await _publish_assistant_trace(
                    repository,
                    event_broker,
                    session_id=session_id,
                    assistant_message=assistant_message,
                    entry={
                        "state": "generation.failed",
                        "generation_id": generation_id,
                        "error": str(exc),
                    },
                )
                _record_generation_step(
                    repository,
                    assistant_message=assistant_message,
                    kind="status",
                    phase="failed",
                    status="failed",
                    state="failed",
                    label="Generation failed",
                    safe_summary=str(exc),
                    ended_at=utc_now(),
                    metadata_json={"generation_id": generation_id, "error": str(exc)},
                )
                repository.close_open_generation_steps(
                    generation_id, status="failed", state="failed"
                )
                await _publish_generation_failed(
                    event_broker,
                    session_id=session_id,
                    generation_id=generation_id,
                    assistant_message_id=assistant_message.id,
                    error_message=str(exc),
                )
        raise
    finally:
        await generation_manager.clear_current_generation(session_id, generation_id)


async def _run_session_worker(
    *,
    db_engine: Engine,
    session_id: str,
    event_broker: SessionEventBroker,
    generation_manager: SessionGenerationManager,
    chat_runtime: ChatRuntime,
    runtime_service: RuntimeService,
    skill_service: SkillService,
    mcp_service: MCPService,
) -> None:
    current_task = asyncio.current_task()
    if current_task is None:
        return
    worker_id = f"session-worker-{uuid4()}"

    terminal_error: ChatRuntimeError | ChatRuntimeConfigurationError | None = None

    try:
        while True:
            with DBSession(db_engine) as worker_db_session:
                repository = SessionRepository(worker_db_session)
                generation = repository.claim_next_generation(session_id, worker_id=worker_id)

            if generation is None:
                break

            try:
                assistant_message_id = await _process_generation(
                    db_engine=db_engine,
                    session_id=session_id,
                    generation_id=generation.id,
                    event_broker=event_broker,
                    generation_manager=generation_manager,
                    chat_runtime=chat_runtime,
                    runtime_service=runtime_service,
                    skill_service=skill_service,
                    mcp_service=mcp_service,
                )
                await generation_manager.resolve_future(
                    session_id, generation.id, assistant_message_id
                )
            except GenerationCancelledError as exc:
                await generation_manager.reject_future(session_id, generation.id, exc)
                continue
            except ChatRuntimeConfigurationError as exc:
                terminal_error = exc
                await generation_manager.reject_future(session_id, generation.id, exc)
                await generation_manager.reject_pending(session_id, exc)
                await _mark_queued_generations_failed(
                    db_engine,
                    session_id=session_id,
                    error_message=str(exc),
                )
                break
            except ChatRuntimeError as exc:
                terminal_error = exc
                await generation_manager.reject_future(session_id, generation.id, exc)
                await generation_manager.reject_pending(session_id, exc)
                await _mark_queued_generations_failed(
                    db_engine,
                    session_id=session_id,
                    error_message=str(exc),
                )
                break
    finally:
        with DBSession(db_engine) as worker_db_session:
            repository = SessionRepository(worker_db_session)
            session = repository.get_session(session_id)
            if session is not None:
                if terminal_error is not None:
                    session = repository.update_session(session, status=SessionStatus.ERROR)
                    await _publish_session_updated(
                        event_broker,
                        session,
                        error=str(terminal_error),
                    )
                elif (
                    session.status == SessionStatus.RUNNING
                    and repository.get_active_generation(session.id) is None
                ):
                    session = repository.update_session(session, status=SessionStatus.DONE)
                    await _publish_session_updated(
                        event_broker,
                        session,
                        queued_prompt_count=repository.queue_size(session.id),
                    )
        await generation_manager.worker_finished(session_id, current_task)


async def _start_worker_if_needed(
    *,
    db_session: DBSession,
    session_id: str,
    event_broker: SessionEventBroker,
    generation_manager: SessionGenerationManager,
    chat_runtime: ChatRuntime,
    runtime_service: RuntimeService,
    skill_service: SkillService,
    mcp_service: MCPService,
) -> None:
    if not await generation_manager.should_start_worker(session_id):
        return
    db_engine = db_session.get_bind()
    if not isinstance(db_engine, Engine):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database engine is unavailable.",
        )
    worker_task = asyncio.create_task(
        _run_session_worker(
            db_engine=db_engine,
            session_id=session_id,
            event_broker=event_broker,
            generation_manager=generation_manager,
            chat_runtime=chat_runtime,
            runtime_service=runtime_service,
            skill_service=skill_service,
            mcp_service=mcp_service,
        )
    )
    await generation_manager.attach_worker(session_id, worker_task)


async def _await_generation_result(
    *,
    session_id: str,
    generation_id: str,
    future: asyncio.Future[str],
) -> str:
    del session_id
    try:
        return await future
    except GenerationCancelledError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ChatRuntimeConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except ChatRuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


def _ensure_running_session(repository: SessionRepository, session: Session) -> Session | None:
    if session.status == SessionStatus.RUNNING:
        return None
    return repository.update_session(session, status=SessionStatus.RUNNING)


@router.post("/{session_id}/chat", response_model=ChatResponse)
async def create_chat_message(
    session_id: str,
    payload: ChatRequest,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    chat_runtime: ChatRuntime = Depends(get_chat_runtime),
    runtime_service: RuntimeService = Depends(get_runtime_service),
    skill_service: SkillService = Depends(get_skill_service),
    mcp_service: MCPService = Depends(get_mcp_service),
    generation_manager: SessionGenerationManager = Depends(get_generation_manager),
) -> ChatResponse:
    repository = SessionRepository(db_session)
    session = _get_session_or_404(repository, session_id)
    if payload.branch_id is not None:
        branch = repository.get_branch(payload.branch_id)
        if branch is None or branch.session_id != session.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Branch not found")
        session = repository.activate_branch(session, branch)
    else:
        branch = repository.ensure_active_branch(session)

    running_session = _ensure_running_session(repository, session)
    if running_session is not None:
        session = running_session
        await _publish_session_updated(event_broker, session)

    next_sequence, next_turn_index = repository.get_next_message_slot(branch.id)
    parent_message = (
        repository.get_message(payload.parent_message_id)
        if payload.parent_message_id is not None
        else repository.get_latest_visible_message(branch.id)
    )
    if parent_message is not None and parent_message.session_id != session.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Parent message not found"
        )
    user_message = repository.create_message(
        session=session,
        role=MessageRole.USER,
        content=payload.content,
        attachments=attachments_to_storage(payload.attachments),
        parent_message_id=parent_message.id if parent_message is not None else None,
        branch_id=branch.id,
        status=MessageStatus.COMPLETED,
        message_kind=MessageKind.MESSAGE,
        sequence=next_sequence,
        turn_index=next_turn_index,
    )
    assistant_message = repository.create_message(
        session=session,
        role=MessageRole.ASSISTANT,
        content="",
        attachments=[],
        parent_message_id=user_message.id,
        branch_id=branch.id,
        status=MessageStatus.PENDING,
        message_kind=MessageKind.MESSAGE,
        sequence=next_sequence + 1,
        turn_index=next_turn_index,
    )
    generation = repository.create_generation(
        session_id=session.id,
        branch_id=branch.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        action=GenerationAction.REPLY,
        metadata_json={
            "operation": "chat",
            "token_budget": payload.token_budget,
            "parent_message_id": payload.parent_message_id,
        },
    )
    repository.update_message(assistant_message, generation_id=generation.id)
    repository.create_generation_step(
        generation_id=generation.id,
        session_id=session.id,
        message_id=assistant_message.id,
        kind="status",
        phase="planning",
        status="pending",
        state="queued",
        label="Generation queued",
        safe_summary="Generation accepted and queued.",
        metadata_json={"operation": "chat"},
    )

    await _publish_message_event(
        event_broker,
        event_type=SessionEventType.MESSAGE_CREATED,
        session_id=session.id,
        message=user_message,
    )
    await _publish_message_event(
        event_broker,
        event_type=SessionEventType.MESSAGE_CREATED,
        session_id=session.id,
        message=assistant_message,
    )

    active_generation_id, queue_position, queued_generation_count = _build_queue_metadata(
        repository,
        session.id,
        generation.id,
    )
    future = (
        await generation_manager.register_future(session.id, generation.id)
        if payload.wait_for_completion
        else None
    )
    should_start_worker = await generation_manager.should_start_worker(session.id)
    if active_generation_id is not None or not should_start_worker:
        await _publish_session_updated(
            event_broker,
            session,
            queued_prompt_count=max(1, queued_generation_count),
        )
    await _start_worker_if_needed(
        db_session=db_session,
        session_id=session.id,
        event_broker=event_broker,
        generation_manager=generation_manager,
        chat_runtime=chat_runtime,
        runtime_service=runtime_service,
        skill_service=skill_service,
        mcp_service=mcp_service,
    )

    if not payload.wait_for_completion:
        db_session.expire_all()
        refreshed_session = _get_session_or_404(repository, session_id)
        return ChatResponse(
            session=to_session_read(refreshed_session),
            user_message=to_message_read(user_message),
            assistant_message=to_message_read(assistant_message),
            generation=_build_generation_read(repository, generation),
            branch=to_conversation_branch_read(branch),
            queue_position=queue_position,
            active_generation_id=active_generation_id,
            queued_generation_count=queued_generation_count,
        )

    if future is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Generation future was not registered.",
        )
    await _await_generation_result(
        session_id=session.id, generation_id=generation.id, future=future
    )
    db_session.expire_all()
    refreshed_session = _get_session_or_404(repository, session_id)
    refreshed_generation = repository.get_generation(generation.id)
    refreshed_assistant_message = repository.get_message(assistant_message.id)
    if refreshed_generation is None or refreshed_assistant_message is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Assistant message was not persisted.",
        )
    active_generation_id, queue_position, queued_generation_count = _build_queue_metadata(
        repository,
        session.id,
        generation.id,
    )
    return ChatResponse(
        session=to_session_read(refreshed_session),
        user_message=to_message_read(user_message),
        assistant_message=to_message_read(refreshed_assistant_message),
        generation=_build_generation_read(repository, refreshed_generation),
        branch=to_conversation_branch_read(branch),
        queue_position=queue_position,
        active_generation_id=active_generation_id,
        queued_generation_count=queued_generation_count,
    )


@router.post("/{session_id}/messages/{message_id}/edit", response_model=MessageMutationResponse)
async def edit_message(
    session_id: str,
    message_id: str,
    payload: MessageEditRequest,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    chat_runtime: ChatRuntime = Depends(get_chat_runtime),
    runtime_service: RuntimeService = Depends(get_runtime_service),
    skill_service: SkillService = Depends(get_skill_service),
    mcp_service: MCPService = Depends(get_mcp_service),
    generation_manager: SessionGenerationManager = Depends(get_generation_manager),
) -> MessageMutationResponse:
    repository = SessionRepository(db_session)
    session = _get_session_or_404(repository, session_id)
    target_message = _get_message_or_404(repository, session_id=session_id, message_id=message_id)
    if target_message.role != MessageRole.USER:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Only user messages can be edited."
        )
    branch_id = payload.branch_id or target_message.branch_id
    branch = repository.get_branch(branch_id or "")
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Message branch was not found."
        )

    running_session = _ensure_running_session(repository, session)
    if running_session is not None:
        session = running_session
        await _publish_session_updated(event_broker, session)

    repository.supersede_branch_descendants(
        branch_id=branch.id,
        sequence=target_message.sequence,
        inclusive=True,
    )
    edited_message = repository.create_message(
        session=session,
        role=MessageRole.USER,
        content=payload.content,
        attachments=attachments_to_storage(payload.attachments),
        parent_message_id=target_message.parent_message_id,
        branch_id=branch.id,
        status=MessageStatus.COMPLETED,
        message_kind=MessageKind.MESSAGE,
        sequence=target_message.sequence,
        turn_index=target_message.turn_index,
        edited_from_message_id=target_message.id,
        version_group_id=target_message.version_group_id or target_message.id,
    )
    assistant_group_id = _find_sibling_version_group_id(
        repository,
        session_id=session.id,
        branch_id=branch.id,
        sequence=target_message.sequence + 1,
        role=MessageRole.ASSISTANT,
    )
    assistant_message = repository.create_message(
        session=session,
        role=MessageRole.ASSISTANT,
        content="",
        attachments=[],
        parent_message_id=edited_message.id,
        branch_id=branch.id,
        status=MessageStatus.PENDING,
        message_kind=MessageKind.MESSAGE,
        sequence=target_message.sequence + 1,
        turn_index=target_message.turn_index,
        version_group_id=assistant_group_id,
    )
    generation = repository.create_generation(
        session_id=session.id,
        branch_id=branch.id,
        user_message_id=edited_message.id,
        assistant_message_id=assistant_message.id,
        action=GenerationAction.EDIT,
        target_message_id=target_message.id,
        metadata_json={
            "operation": "edit",
            "edited_message_id": target_message.id,
            "token_budget": payload.token_budget,
        },
    )
    repository.update_message(assistant_message, generation_id=generation.id)
    repository.create_generation_step(
        generation_id=generation.id,
        session_id=session.id,
        message_id=assistant_message.id,
        kind="status",
        phase="planning",
        status="pending",
        state="queued",
        label="Generation queued",
        safe_summary="Generation accepted and queued.",
        metadata_json={"operation": "edit"},
    )
    session = repository.activate_branch(session, branch)

    await _publish_message_event(
        event_broker,
        event_type=SessionEventType.MESSAGE_CREATED,
        session_id=session.id,
        message=edited_message,
    )
    await _publish_message_event(
        event_broker,
        event_type=SessionEventType.MESSAGE_CREATED,
        session_id=session.id,
        message=assistant_message,
    )

    future = await generation_manager.register_future(session.id, generation.id)
    await _start_worker_if_needed(
        db_session=db_session,
        session_id=session.id,
        event_broker=event_broker,
        generation_manager=generation_manager,
        chat_runtime=chat_runtime,
        runtime_service=runtime_service,
        skill_service=skill_service,
        mcp_service=mcp_service,
    )
    await _await_generation_result(
        session_id=session.id, generation_id=generation.id, future=future
    )
    db_session.expire_all()
    refreshed_session = _get_session_or_404(repository, session_id)
    refreshed_branch = repository.get_branch(branch.id)
    refreshed_assistant = repository.get_message(assistant_message.id)
    refreshed_generation = repository.get_generation(generation.id)
    if refreshed_branch is None or refreshed_assistant is None or refreshed_generation is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Mutation state was not persisted.",
        )
    return MessageMutationResponse(
        session=to_session_read(refreshed_session),
        branch=to_conversation_branch_read(refreshed_branch),
        user_message=to_message_read(edited_message),
        assistant_message=to_message_read(refreshed_assistant),
        generation=_build_generation_read(repository, refreshed_generation),
    )


@router.post(
    "/{session_id}/messages/{message_id}/regenerate",
    response_model=MessageMutationResponse,
)
async def regenerate_message(
    session_id: str,
    message_id: str,
    payload: MessageRegenerateRequest | None = None,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    chat_runtime: ChatRuntime = Depends(get_chat_runtime),
    runtime_service: RuntimeService = Depends(get_runtime_service),
    skill_service: SkillService = Depends(get_skill_service),
    mcp_service: MCPService = Depends(get_mcp_service),
    generation_manager: SessionGenerationManager = Depends(get_generation_manager),
) -> MessageMutationResponse:
    repository = SessionRepository(db_session)
    session = _get_session_or_404(repository, session_id)
    target_message = _get_message_or_404(repository, session_id=session_id, message_id=message_id)
    if target_message.role != MessageRole.ASSISTANT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only assistant messages can be regenerated.",
        )
    branch_id = (
        payload.branch_id
        if payload is not None and payload.branch_id is not None
        else target_message.branch_id
    )
    branch = repository.get_branch(branch_id or "")
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Message branch was not found."
        )

    running_session = _ensure_running_session(repository, session)
    if running_session is not None:
        session = running_session
        await _publish_session_updated(event_broker, session)

    repository.supersede_branch_descendants(
        branch_id=branch.id,
        sequence=target_message.sequence,
        inclusive=True,
    )
    assistant_message = repository.create_message(
        session=session,
        role=MessageRole.ASSISTANT,
        content="",
        attachments=[],
        parent_message_id=target_message.parent_message_id,
        branch_id=branch.id,
        status=MessageStatus.PENDING,
        message_kind=MessageKind.MESSAGE,
        sequence=target_message.sequence,
        turn_index=target_message.turn_index,
        version_group_id=target_message.version_group_id or target_message.id,
    )
    generation = repository.create_generation(
        session_id=session.id,
        branch_id=branch.id,
        user_message_id=target_message.parent_message_id,
        assistant_message_id=assistant_message.id,
        action=GenerationAction.REGENERATE,
        target_message_id=target_message.id,
        metadata_json={
            "operation": "regenerate",
            "regenerated_message_id": target_message.id,
            "token_budget": payload.token_budget if payload is not None else None,
        },
    )
    repository.update_message(assistant_message, generation_id=generation.id)
    repository.create_generation_step(
        generation_id=generation.id,
        session_id=session.id,
        message_id=assistant_message.id,
        kind="status",
        phase="planning",
        status="pending",
        state="queued",
        label="Generation queued",
        safe_summary="Generation accepted and queued.",
        metadata_json={"operation": "regenerate"},
    )
    session = repository.activate_branch(session, branch)

    await _publish_message_event(
        event_broker,
        event_type=SessionEventType.MESSAGE_CREATED,
        session_id=session.id,
        message=assistant_message,
    )

    future = await generation_manager.register_future(session.id, generation.id)
    await _start_worker_if_needed(
        db_session=db_session,
        session_id=session.id,
        event_broker=event_broker,
        generation_manager=generation_manager,
        chat_runtime=chat_runtime,
        runtime_service=runtime_service,
        skill_service=skill_service,
        mcp_service=mcp_service,
    )
    await _await_generation_result(
        session_id=session.id, generation_id=generation.id, future=future
    )
    db_session.expire_all()
    refreshed_session = _get_session_or_404(repository, session_id)
    refreshed_branch = repository.get_branch(branch.id)
    refreshed_assistant = repository.get_message(assistant_message.id)
    refreshed_generation = repository.get_generation(generation.id)
    if refreshed_branch is None or refreshed_assistant is None or refreshed_generation is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Mutation state was not persisted.",
        )
    return MessageMutationResponse(
        session=to_session_read(refreshed_session),
        branch=to_conversation_branch_read(refreshed_branch),
        assistant_message=to_message_read(refreshed_assistant),
        generation=_build_generation_read(repository, refreshed_generation),
    )


@router.post("/{session_id}/messages/{message_id}/fork", response_model=SessionConversationRead)
async def fork_from_message(
    session_id: str,
    message_id: str,
    payload: BranchForkRequest | None = None,
    db_session: DBSession = Depends(get_db_session),
) -> SessionConversationRead:
    repository = SessionRepository(db_session)
    session = _get_session_or_404(repository, session_id)
    target_message = _get_message_or_404(repository, session_id=session_id, message_id=message_id)
    source_branch = repository.get_branch(target_message.branch_id or "")
    if source_branch is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Message branch was not found."
        )
    new_branch = repository.create_branch(
        session=session,
        parent_branch_id=source_branch.id,
        forked_from_message_id=target_message.id,
        name=payload.name if payload is not None else None,
    )
    repository.clone_branch_path_to_message(
        session=session,
        source_branch_id=source_branch.id,
        target_message=target_message,
        new_branch=new_branch,
    )
    session = repository.activate_branch(session, new_branch)
    db_session.expire_all()
    refreshed_session = _get_session_or_404(repository, session_id)
    return _build_conversation_read(repository, refreshed_session)


@router.post("/{session_id}/messages/{message_id}/rollback", response_model=SessionConversationRead)
async def rollback_to_message(
    session_id: str,
    message_id: str,
    payload: MessageRollbackRequest | None = None,
    db_session: DBSession = Depends(get_db_session),
) -> SessionConversationRead:
    repository = SessionRepository(db_session)
    session = _get_session_or_404(repository, session_id)
    target_message = _get_message_or_404(repository, session_id=session_id, message_id=message_id)
    branch_id = (
        payload.branch_id
        if payload is not None and payload.branch_id is not None
        else target_message.branch_id
    )
    branch = repository.get_branch(branch_id or "")
    if branch is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Message branch was not found."
        )
    repository.supersede_branch_descendants(
        branch_id=branch.id,
        sequence=target_message.sequence,
        inclusive=False,
    )
    session = repository.activate_branch(session, branch)
    db_session.expire_all()
    refreshed_session = _get_session_or_404(repository, session_id)
    return _build_conversation_read(repository, refreshed_session)


@router.post(
    "/{session_id}/generations/{generation_id}/cancel",
    response_model=ChatGenerationRead,
)
async def cancel_generation(
    session_id: str,
    generation_id: str,
    db_session: DBSession = Depends(get_db_session),
    generation_manager: SessionGenerationManager = Depends(get_generation_manager),
) -> ChatGenerationRead:
    repository = SessionRepository(db_session)
    _get_session_or_404(repository, session_id)
    generation = repository.get_generation(generation_id)
    if generation is None or generation.session_id != session_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Generation not found")

    assistant_message = repository.get_message(generation.assistant_message_id)
    if generation.status == GenerationStatus.QUEUED:
        repository.cancel_generation(generation, error_message="Queued generation was cancelled.")
        if assistant_message is not None:
            repository.update_message(
                assistant_message,
                status=MessageStatus.CANCELLED,
                error_message="Queued generation was cancelled.",
            )
            _record_generation_step(
                repository,
                assistant_message=assistant_message,
                kind="status",
                phase="cancelled",
                status="cancelled",
                state="cancelled",
                label="Generation cancelled",
                safe_summary="Queued generation was cancelled.",
                ended_at=utc_now(),
                metadata_json={"generation_id": generation.id},
            )
        repository.close_open_generation_steps(generation.id, status="cancelled", state="cancelled")
        await generation_manager.reject_future(
            session_id,
            generation.id,
            GenerationCancelledError("Queued generation was cancelled."),
        )
    elif generation.status == GenerationStatus.RUNNING:
        repository.cancel_generation(generation, error_message="Active generation was cancelled.")
        if assistant_message is not None:
            repository.update_message(
                assistant_message,
                status=MessageStatus.CANCELLED,
                error_message="Active generation was cancelled.",
            )
            _record_generation_step(
                repository,
                assistant_message=assistant_message,
                kind="status",
                phase="cancelled",
                status="cancelled",
                state="cancelled",
                label="Generation cancelled",
                safe_summary="Active generation was cancelled.",
                ended_at=utc_now(),
                metadata_json={"generation_id": generation.id},
            )
        repository.close_open_generation_steps(generation.id, status="cancelled", state="cancelled")
        await generation_manager.cancel_generation(session_id, generation.id)
    refreshed_generation = repository.get_generation(generation.id)
    if refreshed_generation is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Generation state was not persisted.",
        )
    return _build_generation_read(repository, refreshed_generation)
