from __future__ import annotations

from app.agent.transcript_runtime import TranscriptRuntimeService
from app.agent.workspace_rehydrate import build_workspace_context, rehydrate_from_compact_boundary


class PostCompactReinjectionService:
    def __init__(self) -> None:
        self._transcript_runtime = TranscriptRuntimeService()

    def build_reinjection(
        self,
        *,
        compact_runtime: dict[str, object],
        retrieval_summary: str,
        session_memory_summary: str,
        current_stage: str | None,
        task_name: str,
        capability_inventory_summary: str,
        capability_schema_summary: str,
        capability_prompt_fragment: str,
        mutable_state: dict[str, object],
        cycle_id: str,
    ) -> dict[str, object]:
        compact_applied = bool(compact_runtime.get("compacted", False))
        boundary_marker = str(compact_runtime.get("boundary_marker") or "")
        compact_summary = str(compact_runtime.get("compact_summary") or "")
        retained_live_state = self._dict(compact_runtime.get("retained_live_state"))
        boundary_workspace = rehydrate_from_compact_boundary(compact_runtime=compact_runtime)
        workspace_context = build_workspace_context(boundary_workspace)
        workspace_state = self._dict(workspace_context.get("workspace_state"))
        restored_stage = self._string_value(retained_live_state.get("current_stage"))
        restored_task = self._string_value(retained_live_state.get("current_task"))
        effective_stage = (
            restored_stage if compact_applied and restored_stage is not None else current_stage
        )
        effective_task = (
            restored_task if compact_applied and restored_task is not None else task_name
        )
        active_tool_summary = self._active_tool_summary(
            capability_inventory_summary=capability_inventory_summary,
            capability_schema_summary=capability_schema_summary,
        )
        fragments = {
            "capability_inventory_summary": capability_inventory_summary,
            "capability_schema_summary": capability_schema_summary,
            "capability_prompt_fragment": capability_prompt_fragment,
            "retrieval_summary": retrieval_summary,
            "session_memory_summary": session_memory_summary,
            "task_stage_marker": (
                f"Current stage: {effective_stage or 'unknown'} | Current task: {effective_task}"
            ),
            "active_tool_summary": active_tool_summary,
            "compact_summary": compact_summary,
            "workspace_state": workspace_state,
        }
        summary_lines = [
            (f"Compact boundary: {boundary_marker}" if compact_applied and boundary_marker else ""),
            str(fragments["task_stage_marker"]),
            f"Retrieval continuity: {retrieval_summary}",
            f"Session memory continuity: {session_memory_summary}",
            f"Active tool availability summary: {active_tool_summary}",
            (
                f"Workspace continuity: {self._workspace_summary(workspace_state)}"
                if workspace_state
                else ""
            ),
            f"Capability prompt continuity: {capability_prompt_fragment}",
            (f"Compact summary: {compact_summary}" if compact_applied and compact_summary else ""),
        ]
        summary = "\n".join(part for part in summary_lines if part)
        reinjection = {
            "compact_applied": compact_applied,
            "boundary_marker": boundary_marker,
            "summary": summary,
            "fragments": fragments,
            "provenance": {
                "source": "post_compact_reinjection",
                "boundary_marker": boundary_marker,
                "restored_from_boundary": compact_applied,
                "reinjected_components": sorted(fragments.keys()),
                "workspace_state": workspace_state,
                "workspace_rehydrate": workspace_context.get("workspace_rehydrate"),
            },
        }
        self._transcript_runtime.append_reinjection_event(
            mutable_state=mutable_state,
            reinjection=reinjection,
            cycle_id=cycle_id,
            current_stage=current_stage,
            task_name=effective_task,
        )
        return reinjection

    @staticmethod
    def _active_tool_summary(
        *,
        capability_inventory_summary: str,
        capability_schema_summary: str,
    ) -> str:
        if capability_inventory_summary.strip():
            return capability_inventory_summary
        if capability_schema_summary.strip():
            return capability_schema_summary
        return "No active tool availability summary is currently available."

    @staticmethod
    def _dict(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        return {str(key): item for key, item in value.items()}

    @staticmethod
    def _string_value(value: object) -> str | None:
        if isinstance(value, str) and value:
            return value
        return None

    @staticmethod
    def _workspace_summary(workspace_state: dict[str, object]) -> str:
        active_stage = str(workspace_state.get("active_stage") or "unknown")
        active_tasks = workspace_state.get("active_tasks")
        task_text = (
            ", ".join(item for item in active_tasks if isinstance(item, str))
            if isinstance(active_tasks, list)
            else ""
        )
        latest_turn_directive = str(workspace_state.get("latest_turn_directive") or "continue")
        return (
            f"stage={active_stage}; tasks={task_text or 'n/a'}; directive={latest_turn_directive}"
        )
