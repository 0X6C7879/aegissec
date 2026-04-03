from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


def _dict(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items()}


def _dict_list(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


class NextTurnDirective(str, Enum):
    CONTINUE = "continue"
    RETRY_SAME_WAVE = "retry_same_wave"
    REPLAN_SUBGRAPH = "replan_subgraph"
    AWAIT_USER_INPUT = "await_user_input"
    AWAIT_APPROVAL = "await_approval"
    FINALIZE = "finalize"
    STOP_LOOP = "stop_loop"


@dataclass(frozen=True)
class AgentTurn:
    turn_id: str
    cycle_id: str
    phase: str
    current_stage: str | None
    current_task_names: list[str] = field(default_factory=list)
    assistant_reasoning_summary: str = ""
    transcript_delta_id: str | None = None
    next_turn_directive: NextTurnDirective = NextTurnDirective.CONTINUE
    started_at: str | None = None
    ended_at: str | None = None

    def to_state(self) -> dict[str, object]:
        return {
            "turn_id": self.turn_id,
            "cycle_id": self.cycle_id,
            "phase": self.phase,
            "current_stage": self.current_stage,
            "current_task_names": list(self.current_task_names),
            "assistant_reasoning_summary": self.assistant_reasoning_summary,
            "transcript_delta_id": self.transcript_delta_id,
            "next_turn_directive": self.next_turn_directive.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }

    @classmethod
    def from_state(cls, raw: object) -> AgentTurn | None:
        raw_dict = _dict(raw)
        turn_id = raw_dict.get("turn_id")
        cycle_id = raw_dict.get("cycle_id")
        phase = raw_dict.get("phase")
        if (
            not isinstance(turn_id, str)
            or not isinstance(cycle_id, str)
            or not isinstance(phase, str)
        ):
            return None
        directive_raw = raw_dict.get("next_turn_directive")
        try:
            directive = (
                directive_raw
                if isinstance(directive_raw, NextTurnDirective)
                else NextTurnDirective(str(directive_raw or NextTurnDirective.CONTINUE.value))
            )
        except ValueError:
            directive = NextTurnDirective.CONTINUE
        current_stage = raw_dict.get("current_stage")
        current_task_names_raw = raw_dict.get("current_task_names")
        transcript_delta_id = raw_dict.get("transcript_delta_id")
        started_at = raw_dict.get("started_at")
        ended_at = raw_dict.get("ended_at")
        return cls(
            turn_id=turn_id,
            cycle_id=cycle_id,
            phase=phase,
            current_stage=current_stage if isinstance(current_stage, str) else None,
            current_task_names=(
                [item for item in current_task_names_raw if isinstance(item, str)]
                if isinstance(current_task_names_raw, list)
                else []
            ),
            assistant_reasoning_summary=str(raw_dict.get("assistant_reasoning_summary") or ""),
            transcript_delta_id=(
                transcript_delta_id if isinstance(transcript_delta_id, str) else None
            ),
            next_turn_directive=directive,
            started_at=started_at if isinstance(started_at, str) else None,
            ended_at=ended_at if isinstance(ended_at, str) else None,
        )


@dataclass(frozen=True)
class TranscriptDelta:
    delta_id: str
    turn_id: str
    tool_use_blocks: list[dict[str, object]] = field(default_factory=list)
    tool_result_blocks: list[dict[str, object]] = field(default_factory=list)
    tool_error_blocks: list[dict[str, object]] = field(default_factory=list)
    compact_boundary_blocks: list[dict[str, object]] = field(default_factory=list)
    reinjection_blocks: list[dict[str, object]] = field(default_factory=list)
    assistant_blocks: list[dict[str, object]] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    def to_state(self) -> dict[str, object]:
        return {
            "delta_id": self.delta_id,
            "turn_id": self.turn_id,
            "tool_use_blocks": list(self.tool_use_blocks),
            "tool_result_blocks": list(self.tool_result_blocks),
            "tool_error_blocks": list(self.tool_error_blocks),
            "compact_boundary_blocks": list(self.compact_boundary_blocks),
            "reinjection_blocks": list(self.reinjection_blocks),
            "assistant_blocks": list(self.assistant_blocks),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_state(cls, raw: object) -> TranscriptDelta | None:
        raw_dict = _dict(raw)
        delta_id = raw_dict.get("delta_id")
        turn_id = raw_dict.get("turn_id")
        if not isinstance(delta_id, str) or not isinstance(turn_id, str):
            return None
        return cls(
            delta_id=delta_id,
            turn_id=turn_id,
            tool_use_blocks=_dict_list(raw_dict.get("tool_use_blocks")),
            tool_result_blocks=_dict_list(raw_dict.get("tool_result_blocks")),
            tool_error_blocks=_dict_list(raw_dict.get("tool_error_blocks")),
            compact_boundary_blocks=_dict_list(raw_dict.get("compact_boundary_blocks")),
            reinjection_blocks=_dict_list(raw_dict.get("reinjection_blocks")),
            assistant_blocks=_dict_list(raw_dict.get("assistant_blocks")),
            metadata=_dict(raw_dict.get("metadata")),
        )


@dataclass(frozen=True)
class ToolUseRecord:
    trace_id: str
    tool_name: str
    task_id: str
    task_name: str
    cycle_id: str | None
    scheduler_group: str | None
    started_at: str | None

    def to_state(self) -> dict[str, object]:
        return {
            "trace_id": self.trace_id,
            "tool_name": self.tool_name,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "cycle_id": self.cycle_id,
            "scheduler_group": self.scheduler_group,
            "started_at": self.started_at,
        }


@dataclass(frozen=True)
class ToolResultRecord:
    trace_id: str
    tool_name: str
    task_id: str
    task_name: str
    cycle_id: str | None
    status: str
    transcript_block_count: int
    source_type: str = "runtime"
    source_name: str = "workflow.tool"
    command_or_action: str = ""
    input_payload: dict[str, object] = field(default_factory=dict)
    output_payload: dict[str, object] = field(default_factory=dict)
    citations: list[dict[str, object]] = field(default_factory=list)
    artifacts: list[dict[str, object]] = field(default_factory=list)
    started_at: str | None = None
    ended_at: str | None = None

    def to_state(self) -> dict[str, object]:
        return {
            "trace_id": self.trace_id,
            "tool_name": self.tool_name,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "cycle_id": self.cycle_id,
            "status": self.status,
            "transcript_block_count": self.transcript_block_count,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "command_or_action": self.command_or_action,
            "input_payload": dict(self.input_payload),
            "output_payload": dict(self.output_payload),
            "citations": list(self.citations),
            "artifacts": list(self.artifacts),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }
