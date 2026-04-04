from __future__ import annotations

from app.agent.context_models import RetrievalState
from app.agent.transcript_runtime import TranscriptRuntimeService
from app.agent.workspace_state import (
    WorkspaceRehydratedState,
    WorkspaceRehydrateResult,
    WorkspaceRetainedState,
)


def rehydrate_from_compact_boundary(
    *, compact_runtime: dict[str, object]
) -> WorkspaceRehydrateResult:
    retained_live_state = _dict(compact_runtime.get("retained_live_state"))
    retained_workspace = WorkspaceRetainedState.from_state(
        retained_live_state.get("workspace_state")
    )
    active_stage = retained_workspace.active_stage or _string(
        retained_live_state.get("current_stage")
    )
    active_tasks = retained_workspace.active_tasks or _string_tuple(
        retained_live_state.get("current_task")
    )
    rehydrated_state = WorkspaceRehydratedState(
        active_stage=active_stage,
        active_tasks=active_tasks,
        latest_turn_directive=retained_workspace.latest_turn_directive,
        pending_protocol=dict(retained_workspace.pending_protocol),
        recent_transcript_highlights=retained_workspace.recent_transcript_highlights,
        selected_project_memory_entries=retained_workspace.selected_project_memory_entries,
        active_retrieval_focus=dict(retained_workspace.current_retrieval_focus),
        active_capability_summary=retained_workspace.active_capability_inventory_summary,
    )
    used_sources = ["boundary"] if retained_live_state else []
    return WorkspaceRehydrateResult(
        state=rehydrated_state,
        provenance={
            "used_sources": used_sources,
            "boundary_marker": str(compact_runtime.get("boundary_marker") or ""),
        },
        summary=_workspace_summary(rehydrated_state),
    )


def build_rehydrated_workspace(
    *,
    mutable_state: dict[str, object],
    compact_runtime: dict[str, object],
    retrieval: RetrievalState,
    capability_inventory_summary: str,
) -> WorkspaceRehydrateResult:
    transcript_runtime = TranscriptRuntimeService()
    boundary_result = rehydrate_from_compact_boundary(compact_runtime=compact_runtime)
    latest_reinjection = _latest_reinjection_workspace_state(mutable_state)
    latest_assistant_turn = _latest_assistant_turn(mutable_state)
    pause_active = _pause_active(mutable_state)
    recent_transcript_highlights = _recent_transcript_highlights(mutable_state, transcript_runtime)
    selected_project_memory_entries = _selected_project_memory_entries(mutable_state, retrieval)
    active_retrieval_focus = _active_retrieval_focus(mutable_state, retrieval)
    active_capability_summary = (
        capability_inventory_summary
        or _string(latest_reinjection.get("active_capability_summary"))
        or boundary_result.state.active_capability_summary
    )
    latest_turn_directive = (
        _string(latest_assistant_turn.get("resulting_directive"))
        or _string(latest_reinjection.get("latest_turn_directive"))
        or boundary_result.state.latest_turn_directive
        or transcript_runtime.last_directive(mutable_state).value
    )
    active_stage = (
        _string(latest_reinjection.get("active_stage"))
        or _string(mutable_state.get("current_stage"))
        or boundary_result.state.active_stage
    )
    active_tasks = (
        _string_tuple(latest_reinjection.get("active_tasks"))
        or boundary_result.state.active_tasks
        or _string_tuple(mutable_state.get("active_tasks"))
    )
    pending_protocol = (
        pause_active
        or _dict(latest_reinjection.get("pending_protocol"))
        or dict(boundary_result.state.pending_protocol)
    )
    rehydrated_state = WorkspaceRehydratedState(
        active_stage=active_stage,
        active_tasks=active_tasks,
        latest_turn_directive=latest_turn_directive,
        pending_protocol=pending_protocol,
        recent_transcript_highlights=recent_transcript_highlights,
        selected_project_memory_entries=selected_project_memory_entries,
        active_retrieval_focus=active_retrieval_focus,
        active_capability_summary=active_capability_summary,
    )
    return WorkspaceRehydrateResult(
        state=rehydrated_state,
        provenance={
            "used_sources": [
                "boundary",
                "reinjection",
                "transcript",
                "pause",
                "retrieval",
                "assistant_turn",
            ],
            "boundary_marker": str(compact_runtime.get("boundary_marker") or ""),
            "reinjection_event_id": str(_latest_reinjection_event_id(mutable_state) or ""),
        },
        summary=_workspace_summary(rehydrated_state),
    )


def build_workspace_context(result: WorkspaceRehydrateResult) -> dict[str, object]:
    return {
        "workspace_state": result.state.to_state(),
        "workspace_rehydrate": result.to_state(),
    }


def _latest_reinjection_workspace_state(mutable_state: dict[str, object]) -> dict[str, object]:
    transcript_runtime = TranscriptRuntimeService()
    events = transcript_runtime.recent_reinjection_events(mutable_state, limit=1)
    if not events:
        return {}
    provenance = _dict(events[-1].get("provenance"))
    workspace_rehydrate = _dict(provenance.get("workspace_rehydrate"))
    state = _dict(workspace_rehydrate.get("state"))
    return state


def _latest_reinjection_event_id(mutable_state: dict[str, object]) -> str | None:
    transcript_runtime = TranscriptRuntimeService()
    events = transcript_runtime.recent_reinjection_events(mutable_state, limit=1)
    if not events:
        return None
    event_id = events[-1].get("event_id")
    return event_id if isinstance(event_id, str) else None


def _latest_assistant_turn(mutable_state: dict[str, object]) -> dict[str, object]:
    assistant_turn = _dict(mutable_state.get("assistant_turn"))
    return _dict(assistant_turn.get("outcome"))


def _pause_active(mutable_state: dict[str, object]) -> dict[str, object]:
    pause = _dict(mutable_state.get("pause"))
    active = _dict(pause.get("active"))
    if not active:
        return {}
    return {
        "kind": str(active.get("kind") or ""),
        "pause_reason": str(active.get("pause_reason") or ""),
        "resume_condition": str(active.get("resume_condition") or ""),
        "task_id": str(active.get("task_id") or ""),
        "task_name": str(active.get("task_name") or ""),
    }


def _selected_project_memory_entries(
    mutable_state: dict[str, object], retrieval: RetrievalState
) -> tuple[str, ...]:
    selected: list[str] = []
    for item in retrieval.project.items:
        entry_id = item.metadata.get("memory_entry_id")
        if isinstance(entry_id, str) and entry_id:
            selected.append(entry_id)
    if selected:
        return tuple(selected)
    retrieval_manifest = _dict(mutable_state.get("retrieval_manifest"))
    project_manifest = _dict(retrieval_manifest.get("project"))
    sources = project_manifest.get("sources")
    if not isinstance(sources, list):
        return ()
    return tuple(
        str(source.get("source_id") or "")
        for source in sources
        if isinstance(source, dict) and isinstance(source.get("source_id"), str)
    )


def _active_retrieval_focus(
    mutable_state: dict[str, object], retrieval: RetrievalState
) -> dict[str, object]:
    if retrieval.project.items:
        first = retrieval.project.items[0]
        return {
            "scope": "project",
            "focus": str(first.metadata.get("memory_entry_id") or first.record_id),
            "source_count": retrieval.project.source_count,
        }
    retrieval_manifest = _dict(mutable_state.get("retrieval_manifest"))
    project_manifest = _dict(retrieval_manifest.get("project"))
    sources = project_manifest.get("sources")
    if isinstance(sources, list) and sources:
        first_source = sources[0]
        if isinstance(first_source, dict):
            return {
                "scope": str(first_source.get("scope") or "project"),
                "focus": str(first_source.get("source_id") or ""),
                "source_count": len(sources),
            }
    if retrieval.session_local.items:
        return {
            "scope": "session_local",
            "focus": retrieval.session_local.items[0].record_id,
            "source_count": retrieval.session_local.source_count,
        }
    return {"scope": "", "focus": "", "source_count": 0}


def _recent_transcript_highlights(
    mutable_state: dict[str, object], transcript_runtime: TranscriptRuntimeService
) -> tuple[str, ...]:
    highlights: list[str] = []
    for delta in transcript_runtime.recent_deltas(mutable_state, limit=4):
        for key in (
            "tool_result_blocks",
            "tool_error_blocks",
            "assistant_blocks",
            "compact_boundary_blocks",
            "reinjection_blocks",
        ):
            blocks = delta.get(key)
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                content = str(block.get("content") or "").strip()
                if content:
                    highlights.append(content[:160])
                    break
            if len(highlights) >= 4:
                break
        if len(highlights) >= 4:
            break
    return tuple(highlights)


def _workspace_summary(state: WorkspaceRehydratedState) -> str:
    task_text = ", ".join(state.active_tasks) if state.active_tasks else "n/a"
    return (
        f"stage={state.active_stage or 'unknown'}; tasks={task_text}; "
        f"directive={state.latest_turn_directive}"
    )


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
