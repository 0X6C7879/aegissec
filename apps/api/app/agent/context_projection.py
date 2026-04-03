from __future__ import annotations

from app.agent.context_models import (
    ContextProjection,
    ContextRecord,
    MemoryState,
    ProjectionLevel,
    RetrievalState,
)


class ContextProjectionBuilder:
    def build(
        self,
        *,
        state: dict[str, object],
        retrieval: RetrievalState,
        memory: MemoryState,
    ) -> ContextProjection:
        total_records = len(self._dict_list(state.get("archived_execution_records"))) + len(
            self._dict_list(state.get("execution_records"))
        )
        active_level = 1
        if total_records > 2:
            active_level = 2
        if total_records > 5:
            active_level = 3
        if total_records > 10:
            active_level = 4
        if total_records > 20:
            active_level = 5
        level_one = ProjectionLevel(
            level=1,
            label="raw_trace_window",
            summary="Level 1 keeps the current raw execution window.",
            entries=list(memory.working.raw_entries),
        )
        level_two = ProjectionLevel(
            level=2,
            label="cycle_digest",
            summary="Level 2 compresses the current cycle into distilled working memory.",
            entries=list(memory.working.distilled_entries),
        )
        level_three = ProjectionLevel(
            level=3,
            label="session_memory",
            summary="Level 3 projects durable session memory for replay-safe reuse.",
            entries=list(memory.session.distilled_entries),
        )
        level_four_entries = [
            *memory.project.distilled_entries,
            *retrieval.capability.items,
        ]
        level_four = ProjectionLevel(
            level=4,
            label="project_capability",
            summary="Level 4 mixes project memory and reusable capability context.",
            entries=level_four_entries,
        )
        operator_brief = ContextRecord(
            record_id="projection:operator-brief",
            title="Operator brief",
            summary=(
                f"Session retrieval cites {retrieval.session_local.citation_count} sources; "
                f"session memory stores {len(memory.session.distilled_entries)} distilled entries; "
                f"project memory stores {len(memory.project.distilled_entries)} distilled entries; "
                "projection remains an input source across compact runtime boundaries."
            ),
            kind="operator_brief",
            citations=[
                citation
                for entry in (
                    memory.working.distilled_entries[:1]
                    + retrieval.session_local.items[:1]
                    + retrieval.capability.items[:1]
                )
                for citation in entry.citations[:1]
            ],
            metadata={
                "active_level": active_level,
                "context_source": "projection",
                "source_level_count": 5,
            },
        )
        level_five = ProjectionLevel(
            level=5,
            label="operator_brief",
            summary="Level 5 is the most compact model-facing projection.",
            entries=[operator_brief],
        )
        levels = [level_one, level_two, level_three, level_four, level_five]
        return ContextProjection(
            projection_id=f"projection:level-{active_level}",
            active_level=active_level,
            summary=(
                f"Built five projection levels; active level {active_level} selected from "
                f"{total_records} execution record(s). Projection remains a compact-runtime "
                "source before and after reinjection."
            ),
            levels=levels,
        )

    @staticmethod
    def _dict_list(raw: object) -> list[dict[str, object]]:
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]
