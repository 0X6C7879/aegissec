from __future__ import annotations

from dataclasses import dataclass


def _dict(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items()}


def _string(raw: object) -> str | None:
    return raw if isinstance(raw, str) else None


def _string_tuple(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list | tuple):
        return ()
    return tuple(item for item in raw if isinstance(item, str))


@dataclass(frozen=True)
class WorkspaceRetainedState:
    active_stage: str | None
    active_tasks: tuple[str, ...]
    latest_turn_directive: str
    pending_protocol: dict[str, object]
    active_capability_inventory_summary: str
    recent_transcript_highlights: tuple[str, ...]
    selected_project_memory_entries: tuple[str, ...]
    current_retrieval_focus: dict[str, object]
    open_questions: tuple[str, ...] = ()
    carry_forward_context: str = ""

    def to_state(self) -> dict[str, object]:
        return {
            "active_stage": self.active_stage,
            "active_tasks": list(self.active_tasks),
            "latest_turn_directive": self.latest_turn_directive,
            "pending_protocol": dict(self.pending_protocol),
            "active_capability_inventory_summary": self.active_capability_inventory_summary,
            "recent_transcript_highlights": list(self.recent_transcript_highlights),
            "selected_project_memory_entries": list(self.selected_project_memory_entries),
            "current_retrieval_focus": dict(self.current_retrieval_focus),
            "open_questions": list(self.open_questions),
            "carry_forward_context": self.carry_forward_context,
        }

    @classmethod
    def from_state(cls, raw: object) -> WorkspaceRetainedState:
        raw_dict = _dict(raw)
        return cls(
            active_stage=_string(raw_dict.get("active_stage")),
            active_tasks=_string_tuple(raw_dict.get("active_tasks")),
            latest_turn_directive=str(raw_dict.get("latest_turn_directive") or "continue"),
            pending_protocol=_dict(raw_dict.get("pending_protocol")),
            active_capability_inventory_summary=str(
                raw_dict.get("active_capability_inventory_summary") or ""
            ),
            recent_transcript_highlights=_string_tuple(
                raw_dict.get("recent_transcript_highlights")
            ),
            selected_project_memory_entries=_string_tuple(
                raw_dict.get("selected_project_memory_entries")
            ),
            current_retrieval_focus=_dict(raw_dict.get("current_retrieval_focus")),
            open_questions=_string_tuple(raw_dict.get("open_questions")),
            carry_forward_context=str(raw_dict.get("carry_forward_context") or ""),
        )


@dataclass(frozen=True)
class WorkspaceRehydratedState:
    active_stage: str | None
    active_tasks: tuple[str, ...]
    latest_turn_directive: str
    pending_protocol: dict[str, object]
    recent_transcript_highlights: tuple[str, ...]
    selected_project_memory_entries: tuple[str, ...]
    active_retrieval_focus: dict[str, object]
    active_capability_summary: str
    open_questions: tuple[str, ...] = ()
    carry_forward_context: str = ""

    def to_state(self) -> dict[str, object]:
        return {
            "active_stage": self.active_stage,
            "active_tasks": list(self.active_tasks),
            "latest_turn_directive": self.latest_turn_directive,
            "pending_protocol": dict(self.pending_protocol),
            "recent_transcript_highlights": list(self.recent_transcript_highlights),
            "selected_project_memory_entries": list(self.selected_project_memory_entries),
            "active_retrieval_focus": dict(self.active_retrieval_focus),
            "active_capability_summary": self.active_capability_summary,
            "open_questions": list(self.open_questions),
            "carry_forward_context": self.carry_forward_context,
        }


@dataclass(frozen=True)
class WorkspaceRehydrateResult:
    state: WorkspaceRehydratedState
    provenance: dict[str, object]
    summary: str

    def to_state(self) -> dict[str, object]:
        return {
            "state": self.state.to_state(),
            "provenance": dict(self.provenance),
            "summary": self.summary,
        }
