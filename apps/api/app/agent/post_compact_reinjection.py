from __future__ import annotations


class PostCompactReinjectionService:
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
    ) -> dict[str, object]:
        compact_applied = bool(compact_runtime.get("compacted", False))
        boundary_marker = str(compact_runtime.get("boundary_marker") or "")
        compact_summary = str(compact_runtime.get("compact_summary") or "")
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
                f"Current stage: {current_stage or 'unknown'} | Current task: {task_name}"
            ),
            "active_tool_summary": active_tool_summary,
            "compact_summary": compact_summary,
        }
        summary = "\n".join(
            part
            for part in [
                (
                    f"Compact boundary: {boundary_marker}"
                    if compact_applied and boundary_marker
                    else ""
                ),
                fragments["task_stage_marker"],
                f"Retrieval continuity: {retrieval_summary}",
                f"Session memory continuity: {session_memory_summary}",
                f"Active tool availability summary: {active_tool_summary}",
                f"Capability prompt continuity: {capability_prompt_fragment}",
                (
                    f"Compact summary: {compact_summary}"
                    if compact_applied and compact_summary
                    else ""
                ),
            ]
            if part
        )
        return {
            "compact_applied": compact_applied,
            "boundary_marker": boundary_marker,
            "summary": summary,
            "fragments": fragments,
            "provenance": {
                "source": "post_compact_reinjection",
                "boundary_marker": boundary_marker,
                "reinjected_components": sorted(fragments.keys()),
            },
        }

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
