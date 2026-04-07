from __future__ import annotations

from datetime import datetime

from app.db.models import GenerationStep, Message, utc_now
from app.db.repositories import SessionRepository


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
