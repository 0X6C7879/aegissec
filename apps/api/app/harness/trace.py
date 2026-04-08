from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.events import SessionEvent, SessionEventBroker, SessionEventType
from app.db.models import AssistantTranscriptSegmentKind, GenerationStep, Message, utc_now
from app.db.repositories import SessionRepository
from app.harness import events as harness_events

from .generation_events import publish_attack_graph_updated, publish_message_event
from .transcript import (
    THINK_BLOCK_RE,
    append_transcript_segment,
    is_visible_transcript_noise,
    sanitize_persisted_assistant_text,
)


def message_trace_entries(message: Message) -> list[dict[str, object]]:
    raw_trace = message.metadata_json.get("trace")
    if not isinstance(raw_trace, list):
        return []
    return [dict(entry) for entry in raw_trace if isinstance(entry, dict)]


def persist_reasoning_trace_entry(
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

    message_trace_length = len(message_trace_entries(assistant_message))
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


def record_generation_step(
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


def get_or_create_output_step(
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


def infer_trace_phase(entry: dict[str, object]) -> str:
    state = str(entry.get("state") or "")
    if state in {"generation.completed", "summary.updated"}:
        return "completed" if state == "generation.completed" else "planning"
    if state == "generation.cancelled":
        return "cancelled"
    if state == "generation.failed":
        return "failed"
    if state == "generation.started":
        return "planning"
    if state.startswith("skill.autoroute"):
        return "failed" if state == "skill.autoroute.failed" else "planning"
    if state == "tool.started":
        return "tool_running"
    if state in {"tool.finished", "tool.failed"}:
        return "tool_result"
    return "planning"


def infer_trace_status(entry: dict[str, object]) -> str:
    state = str(entry.get("state") or "")
    if state in {"generation.completed", "tool.finished", "summary.updated"}:
        return "completed"
    if state in {"skill.autoroute.selected", "skill.autoroute.finished", "skill.autoroute.skipped"}:
        return "completed"
    if state in {"generation.failed", "tool.failed"}:
        return "failed"
    if state == "skill.autoroute.failed":
        return "failed"
    if state == "generation.cancelled":
        return "cancelled"
    if state in {
        "generation.started",
        "tool.started",
        "skill.autoroute.started",
        "skill.autoroute.executing",
    }:
        return "running"
    return "completed"


def infer_trace_summary(entry: dict[str, object]) -> str | None:
    summary = entry.get("summary")
    if isinstance(summary, str) and summary:
        return summary
    state = str(entry.get("state") or "")
    tool_name = entry.get("tool")
    tool_display = str(tool_name) if isinstance(tool_name, str) and tool_name else "tool"
    if state == "generation.started":
        return "开始生成回复"
    if state == "generation.completed":
        return "本轮生成已完成"
    if state == "generation.cancelled":
        return "当前生成已停止"
    if state == "generation.failed":
        error_value = entry.get("error")
        return (
            str(error_value)
            if isinstance(error_value, str) and error_value
            else "Generation failed."
        )
    if state == "tool.started":
        return None
    if state == "tool.finished":
        return None
    if state == "tool.failed":
        error_value = entry.get("error")
        if isinstance(error_value, str) and error_value:
            return error_value
        return f"{tool_display} failed."
    if state == "skill.autoroute.started":
        return "正在评估可预载技能"
    if state == "skill.autoroute.selected":
        skill_name = entry.get("skill")
        if isinstance(skill_name, str) and skill_name:
            return f"自动选择 {skill_name}"
        return "已自动选择技能"
    if state == "skill.autoroute.executing":
        return None
    if state == "skill.autoroute.finished":
        return None
    if state == "skill.autoroute.skipped":
        reason = entry.get("reason")
        if reason in {
            "当前没有可用技能",
            "当前消息没有可用于技能路由的上下文",
            "没有高置信技能匹配",
        }:
            return None
        if isinstance(reason, str) and reason:
            return f"未自动预载技能：{reason}"
        return "未自动预载技能"
    if state == "skill.autoroute.failed":
        summary_value = entry.get("summary")
        if isinstance(summary_value, str) and summary_value:
            return summary_value
        error_value = entry.get("error")
        if isinstance(error_value, str) and error_value:
            return f"自动预载技能失败：{error_value}"
        return "自动预载技能失败"
    return None


async def publish_assistant_summary(
    repository: SessionRepository,
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    assistant_message: Message,
    summary: str,
    semantic_snapshot: dict[str, Any] | None = None,
) -> None:
    sanitized_summary = sanitize_persisted_assistant_text(summary)
    if not sanitized_summary or is_visible_transcript_noise(sanitized_summary):
        return
    repository.update_message_summary(assistant_message, sanitized_summary)
    append_transcript_segment(
        repository,
        assistant_message=assistant_message,
        kind=AssistantTranscriptSegmentKind.REASONING,
        status="completed",
        title=None,
        text=sanitized_summary,
        metadata_json={"event": SessionEventType.ASSISTANT_SUMMARY.value},
    )
    if assistant_message.generation_id is not None:
        generation = repository.get_generation(assistant_message.generation_id)
        if generation is not None:
            repository.update_generation(generation, reasoning_summary=sanitized_summary)
    record_generation_step(
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
    persist_reasoning_trace_entry(
        repository,
        assistant_message=assistant_message,
        entry={
            "event": SessionEventType.ASSISTANT_SUMMARY.value,
            "state": "summary.updated",
            "summary": sanitized_summary,
            **(
                {"semantic_state": semantic_snapshot}
                if isinstance(semantic_snapshot, dict) and semantic_snapshot
                else {}
            ),
        },
    )
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.ASSISTANT_SUMMARY,
            session_id=session_id,
            payload={
                "message_id": assistant_message.id,
                "summary": sanitized_summary,
                **harness_events.semantic_event_payload(semantic_snapshot),
            },
        )
    )
    await publish_message_event(
        event_broker,
        event_type=SessionEventType.MESSAGE_UPDATED,
        session_id=session_id,
        message=assistant_message,
    )
    await publish_attack_graph_updated(
        event_broker,
        session_id=session_id,
        assistant_message=assistant_message,
        semantic_snapshot=semantic_snapshot,
    )


async def publish_assistant_trace(
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
            sanitized_value = sanitize_persisted_assistant_text(value)
            if THINK_BLOCK_RE.search(value) and not sanitized_value:
                continue
            sanitized_entry[key] = sanitized_value
        else:
            sanitized_entry[key] = value

    persisted_entry = persist_reasoning_trace_entry(
        repository,
        assistant_message=assistant_message,
        entry={"event": SessionEventType.ASSISTANT_TRACE.value, **sanitized_entry},
    )
    record_generation_step(
        repository,
        assistant_message=assistant_message,
        kind="status",
        phase=infer_trace_phase(sanitized_entry),
        status=infer_trace_status(sanitized_entry),
        state=(
            str(sanitized_entry.get("state"))
            if sanitized_entry.get("state") is not None
            else "trace"
        ),
        label="过程更新",
        safe_summary=infer_trace_summary(sanitized_entry),
        metadata_json={key: value for key, value in persisted_entry.items() if key != "summary"},
    )
    trace_state = str(sanitized_entry.get("state") or "")
    visible_trace_summary = infer_trace_summary(sanitized_entry)
    should_append_trace_segment = (
        trace_state.startswith("generation.") or trace_state.startswith("skill.autoroute.")
    ) and bool(visible_trace_summary)
    if should_append_trace_segment:
        from app.db.models import AssistantTranscriptSegmentKind

        transcript_kind = (
            AssistantTranscriptSegmentKind.ERROR
            if trace_state in {"generation.failed", "skill.autoroute.failed"}
            else AssistantTranscriptSegmentKind.STATUS
        )
        append_transcript_segment(
            repository,
            assistant_message=assistant_message,
            kind=transcript_kind,
            status=infer_trace_status(sanitized_entry),
            title=None,
            text=visible_trace_summary,
            metadata_json={key: value for key, value in persisted_entry.items()},
        )
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.ASSISTANT_TRACE,
            session_id=session_id,
            payload={"message_id": assistant_message.id, **persisted_entry},
        )
    )
    if should_append_trace_segment:
        await publish_message_event(
            event_broker,
            event_type=SessionEventType.MESSAGE_UPDATED,
            session_id=session_id,
            message=assistant_message,
        )
