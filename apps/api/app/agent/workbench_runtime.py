from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


def _dict(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items()}


def _string_list(raw: object) -> list[str]:
    if not isinstance(raw, list | tuple):
        return []
    return [item for item in raw if isinstance(item, str)]


@dataclass(frozen=True)
class WorkbenchRuntimeProvenance:
    source: str
    used_sources: list[str] = field(default_factory=list)
    boundary_marker: str = ""
    compact_boundary_marker: str = ""
    reinjection_event_id: str = ""
    recent_delta_ids: list[str] = field(default_factory=list)
    continuation_ids: list[str] = field(default_factory=list)
    assistant_turn_id: str | None = None
    retrieval_manifest_sources: list[str] = field(default_factory=list)
    memory_entry_ids: list[str] = field(default_factory=list)
    updated_at: str = ""

    def to_state(self) -> dict[str, object]:
        return {
            "source": self.source,
            "used_sources": list(self.used_sources),
            "boundary_marker": self.boundary_marker,
            "compact_boundary_marker": self.compact_boundary_marker,
            "reinjection_event_id": self.reinjection_event_id,
            "recent_delta_ids": list(self.recent_delta_ids),
            "continuation_ids": list(self.continuation_ids),
            "assistant_turn_id": self.assistant_turn_id,
            "retrieval_manifest_sources": list(self.retrieval_manifest_sources),
            "memory_entry_ids": list(self.memory_entry_ids),
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class WorkspaceRuntimeState:
    active_stage: str | None
    active_tasks: list[str]
    current_turn_id: str | None
    latest_directive: str
    active_continuations: list[dict[str, object]]
    active_recall_focus: dict[str, object]
    active_memory_selection: list[str]
    recent_transcript_highlights: list[str]
    active_capability_summary: str
    open_questions: list[str]
    carry_forward_context: str
    pending_protocol_summary: dict[str, object]
    latest_assimilation_summary: dict[str, object]

    def to_state(self) -> dict[str, object]:
        return {
            "active_stage": self.active_stage,
            "active_tasks": list(self.active_tasks),
            "current_turn_id": self.current_turn_id,
            "latest_directive": self.latest_directive,
            "active_continuations": [dict(item) for item in self.active_continuations],
            "active_recall_focus": dict(self.active_recall_focus),
            "active_memory_selection": list(self.active_memory_selection),
            "recent_transcript_highlights": list(self.recent_transcript_highlights),
            "active_capability_summary": self.active_capability_summary,
            "open_questions": list(self.open_questions),
            "carry_forward_context": self.carry_forward_context,
            "pending_protocol_summary": dict(self.pending_protocol_summary),
            "latest_assimilation_summary": dict(self.latest_assimilation_summary),
        }

    @classmethod
    def from_state(cls, raw: object) -> WorkspaceRuntimeState | None:
        raw_dict = _dict(raw)
        raw_latest_directive = raw_dict.get("latest_directive")
        if not isinstance(raw_latest_directive, str):
            return None
        raw_active_stage = raw_dict.get("active_stage")
        raw_current_turn_id = raw_dict.get("current_turn_id")
        return cls(
            active_stage=raw_active_stage if isinstance(raw_active_stage, str) else None,
            active_tasks=_string_list(raw_dict.get("active_tasks")),
            current_turn_id=raw_current_turn_id if isinstance(raw_current_turn_id, str) else None,
            latest_directive=raw_latest_directive,
            active_continuations=(
                [dict(item) for item in raw_active_continuations if isinstance(item, dict)]
                if isinstance(
                    (raw_active_continuations := raw_dict.get("active_continuations")), list
                )
                else []
            ),
            active_recall_focus=_dict(raw_dict.get("active_recall_focus")),
            active_memory_selection=_string_list(raw_dict.get("active_memory_selection")),
            recent_transcript_highlights=_string_list(raw_dict.get("recent_transcript_highlights")),
            active_capability_summary=str(raw_dict.get("active_capability_summary") or ""),
            open_questions=_string_list(raw_dict.get("open_questions")),
            carry_forward_context=str(raw_dict.get("carry_forward_context") or ""),
            pending_protocol_summary=_dict(raw_dict.get("pending_protocol_summary")),
            latest_assimilation_summary=_dict(raw_dict.get("latest_assimilation_summary")),
        )


@dataclass(frozen=True)
class WorkbenchRuntimeBuildResult:
    state: WorkspaceRuntimeState
    provenance: WorkbenchRuntimeProvenance
    summary: str

    def to_state(self) -> dict[str, object]:
        return {
            "state": self.state.to_state(),
            "provenance": self.provenance.to_state(),
            "summary": self.summary,
        }


def build_workbench_runtime(
    *,
    mutable_state: dict[str, object],
    active_stage: str | None,
    active_tasks: list[str],
    latest_directive: str,
    active_continuations: list[dict[str, object]],
    active_recall_focus: dict[str, object],
    active_memory_selection: list[str],
    recent_transcript_highlights: list[str],
    active_capability_summary: str,
    open_questions: list[str],
    carry_forward_context: str,
    pending_protocol_summary: dict[str, object],
    latest_assimilation_summary: dict[str, object],
    workspace_rehydrate: dict[str, object],
    recent_delta_ids: list[str] | None = None,
    continuation_ids: list[str] | None = None,
    assistant_turn_id: str | None = None,
    retrieval_manifest_sources: list[str] | None = None,
    memory_entry_ids: list[str] | None = None,
) -> WorkbenchRuntimeBuildResult:
    assistant_turn = _dict(mutable_state.get("assistant_turn"))
    outcome = _dict(assistant_turn.get("outcome"))
    provenance = WorkbenchRuntimeProvenance(
        source="workflow.loop_engine",
        used_sources=[
            "workbench_runtime",
            "assistant_turn",
            "pause",
            "retrieval",
            "workspace_rehydrate",
        ],
        boundary_marker=str(
            _dict(workspace_rehydrate.get("provenance")).get("boundary_marker") or ""
        ),
        compact_boundary_marker=str(
            _dict(workspace_rehydrate.get("provenance")).get("boundary_marker") or ""
        ),
        reinjection_event_id=str(
            _dict(workspace_rehydrate.get("provenance")).get("reinjection_event_id") or ""
        ),
        recent_delta_ids=list(recent_delta_ids or []),
        continuation_ids=list(continuation_ids or []),
        assistant_turn_id=assistant_turn_id,
        retrieval_manifest_sources=list(retrieval_manifest_sources or []),
        memory_entry_ids=list(memory_entry_ids or []),
        updated_at=datetime.now(UTC).isoformat(),
    )
    state = WorkspaceRuntimeState(
        active_stage=active_stage,
        active_tasks=list(active_tasks),
        current_turn_id=(
            str(outcome.get("turn_id")) if isinstance(outcome.get("turn_id"), str) else None
        ),
        latest_directive=latest_directive,
        active_continuations=[dict(item) for item in active_continuations],
        active_recall_focus=dict(active_recall_focus),
        active_memory_selection=list(active_memory_selection),
        recent_transcript_highlights=list(recent_transcript_highlights),
        active_capability_summary=active_capability_summary,
        open_questions=list(open_questions),
        carry_forward_context=carry_forward_context,
        pending_protocol_summary=dict(pending_protocol_summary),
        latest_assimilation_summary=dict(latest_assimilation_summary),
    )
    task_summary = ", ".join(active_tasks) if active_tasks else "n/a"
    summary = (
        f"stage={active_stage or 'unknown'}; tasks={task_summary}; "
        f"directive={latest_directive}; continuations={len(active_continuations)}"
    )
    build = WorkbenchRuntimeBuildResult(state=state, provenance=provenance, summary=summary)
    mutable_state["workbench_runtime"] = build.to_state()
    return build


def project_workbench_runtime(
    build: WorkbenchRuntimeBuildResult,
    *,
    workspace_rehydrate: dict[str, object],
) -> dict[str, object]:
    runtime_payload = build.to_state()
    projected_workspace_state = project_workspace_state_from_runtime_payload(runtime_payload)
    projected_workspace_rehydrate = project_workspace_rehydrate_from_runtime_payload(
        runtime_payload,
        fallback=workspace_rehydrate,
    )
    return {
        "workbench_runtime": runtime_payload,
        "workspace_state": projected_workspace_state,
        "workspace_rehydrate": projected_workspace_rehydrate,
    }


def workbench_context(build: WorkbenchRuntimeBuildResult) -> dict[str, object]:
    runtime_payload = build.to_state()
    return {
        "workbench_runtime": runtime_payload,
        "workspace_state": project_workspace_state_from_runtime_payload(runtime_payload),
    }


def project_workspace_state_from_runtime_payload(
    runtime_payload: dict[str, object],
    *,
    fallback: dict[str, object] | None = None,
) -> dict[str, object]:
    runtime_state = WorkspaceRuntimeState.from_state(_dict(runtime_payload.get("state")))
    if runtime_state is not None:
        return {
            "active_stage": runtime_state.active_stage,
            "active_tasks": list(runtime_state.active_tasks),
            "latest_turn_directive": runtime_state.latest_directive,
            "pending_protocol": dict(runtime_state.pending_protocol_summary),
            "recent_transcript_highlights": list(runtime_state.recent_transcript_highlights),
            "selected_project_memory_entries": list(runtime_state.active_memory_selection),
            "active_retrieval_focus": dict(runtime_state.active_recall_focus),
            "active_capability_summary": runtime_state.active_capability_summary,
            "open_questions": list(runtime_state.open_questions),
            "carry_forward_context": runtime_state.carry_forward_context,
        }

    runtime_workspace_rehydrate = _dict(runtime_payload.get("workspace_rehydrate"))
    runtime_workspace_rehydrate_state = _dict(runtime_workspace_rehydrate.get("state"))
    if runtime_workspace_rehydrate_state:
        return runtime_workspace_rehydrate_state

    return dict(fallback) if isinstance(fallback, dict) else {}


def project_workspace_rehydrate_from_runtime_payload(
    runtime_payload: dict[str, object],
    *,
    fallback: dict[str, object] | None = None,
) -> dict[str, object]:
    fallback_dict = dict(fallback) if isinstance(fallback, dict) else {}
    projected_state = project_workspace_state_from_runtime_payload(
        runtime_payload,
        fallback=_dict(fallback_dict.get("state")),
    )
    provenance = _dict(fallback_dict.get("provenance"))
    raw_used_sources = provenance.get("used_sources")
    used_sources_list = raw_used_sources if isinstance(raw_used_sources, list) else []
    used_sources = [
        str(item) for item in used_sources_list if isinstance(item, str) and str(item).strip()
    ]
    if "workbench_runtime" not in used_sources:
        used_sources = ["workbench_runtime", *used_sources]
    provenance["used_sources"] = used_sources
    if not provenance.get("boundary_marker"):
        runtime_provenance = _dict(runtime_payload.get("provenance"))
        provenance["boundary_marker"] = str(runtime_provenance.get("boundary_marker") or "")
    summary = str(fallback_dict.get("summary") or "")
    if not summary and projected_state:
        projected_tasks_raw = projected_state.get("active_tasks")
        projected_tasks = projected_tasks_raw if isinstance(projected_tasks_raw, list) else []
        task_text = ", ".join(item for item in projected_tasks if isinstance(item, str))
        summary = (
            f"stage={str(projected_state.get('active_stage') or 'unknown')}; "
            f"tasks={task_text or 'n/a'}; "
            f"directive={str(projected_state.get('latest_turn_directive') or 'continue')}"
        )
    return {
        "state": projected_state,
        "provenance": provenance,
        "summary": summary,
    }


def load_workbench_runtime_state(mutable_state: dict[str, object]) -> WorkspaceRuntimeState | None:
    raw = _dict(mutable_state.get("workbench_runtime"))
    state = raw.get("state")
    parsed = WorkspaceRuntimeState.from_state(state)
    if parsed is not None:
        return parsed
    workspace_rehydrate = _dict(raw.get("workspace_rehydrate"))
    fallback_state = workspace_rehydrate.get("state")
    return WorkspaceRuntimeState.from_state(
        {
            "active_stage": (
                _dict(fallback_state).get("active_stage")
                if isinstance(fallback_state, dict)
                else None
            ),
            "active_tasks": (
                _dict(fallback_state).get("active_tasks")
                if isinstance(fallback_state, dict)
                else []
            ),
            "current_turn_id": None,
            "latest_directive": (
                str(_dict(fallback_state).get("latest_turn_directive") or "continue")
                if isinstance(fallback_state, dict)
                else "continue"
            ),
            "active_continuations": [],
            "active_recall_focus": (
                _dict(fallback_state).get("active_retrieval_focus")
                if isinstance(fallback_state, dict)
                else {}
            ),
            "active_memory_selection": (
                _dict(fallback_state).get("selected_project_memory_entries")
                if isinstance(fallback_state, dict)
                else []
            ),
            "recent_transcript_highlights": (
                _dict(fallback_state).get("recent_transcript_highlights")
                if isinstance(fallback_state, dict)
                else []
            ),
            "active_capability_summary": str(
                _dict(fallback_state).get("active_capability_summary")
                if isinstance(fallback_state, dict)
                else ""
            ),
            "open_questions": [],
            "carry_forward_context": "",
            "pending_protocol_summary": (
                _dict(fallback_state).get("pending_protocol")
                if isinstance(fallback_state, dict)
                else {}
            ),
            "latest_assimilation_summary": {},
        }
    )


def persist_workbench_runtime_state(
    mutable_state: dict[str, object],
    *,
    state: WorkspaceRuntimeState,
    workspace_rehydrate: dict[str, object],
    source: str,
) -> dict[str, object]:
    build = WorkbenchRuntimeBuildResult(
        state=state,
        provenance=WorkbenchRuntimeProvenance(
            source=source,
            used_sources=["workbench_runtime", "workspace_rehydrate"],
            boundary_marker=str(
                _dict(workspace_rehydrate.get("provenance")).get("boundary_marker") or ""
            ),
            compact_boundary_marker=str(
                _dict(workspace_rehydrate.get("provenance")).get("boundary_marker") or ""
            ),
            reinjection_event_id=str(
                _dict(workspace_rehydrate.get("provenance")).get("reinjection_event_id") or ""
            ),
            updated_at=datetime.now(UTC).isoformat(),
        ),
        summary=(
            f"stage={state.active_stage or 'unknown'}; "
            f"tasks={', '.join(state.active_tasks) if state.active_tasks else 'n/a'}; "
            f"directive={state.latest_directive}"
        ),
    )
    runtime = build.to_state()
    runtime["workspace_rehydrate"] = dict(workspace_rehydrate)
    mutable_state["workbench_runtime"] = runtime
    return runtime
