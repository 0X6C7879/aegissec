from __future__ import annotations

from datetime import UTC, datetime

from app.agent.context_models import (
    CitationPointer,
    ContextRecord,
    MemoryLayer,
    MemoryOperation,
    MemoryState,
    RetrievalState,
)
from app.db.models import Session


class MemoryManager:
    def build(
        self,
        *,
        session: Session | None,
        state: dict[str, object],
        retrieval: RetrievalState,
    ) -> MemoryState:
        previous = MemoryState.from_state(self._context_dict(state).get("memory"))
        batch_cycle = self._batch_cycle(state)
        working_raw = self._build_working_raw_entries(state=state, batch_cycle=batch_cycle)
        working_distilled = self._build_working_distilled_entries(working_raw, retrieval)
        session_raw = self._merge_entries(previous.session.raw_entries, working_raw)
        session_distilled = self._merge_entries(
            previous.session.distilled_entries, working_distilled
        )
        promotions = [
            MemoryOperation(
                entry_id=entry.record_id,
                from_layer="working",
                to_layer="session",
                status="promoted",
            )
            for entry in working_distilled
        ]
        project_entries = list(previous.project.distilled_entries)
        project_promotions: list[MemoryOperation] = []
        if session is not None and session.project_id is not None:
            project_entries = self._merge_entries(project_entries, working_distilled)
            project_promotions = [
                MemoryOperation(
                    entry_id=entry.record_id,
                    from_layer="session",
                    to_layer="project",
                    status="promoted",
                )
                for entry in working_distilled
            ]
        elif working_distilled:
            project_promotions = [
                MemoryOperation(
                    entry_id=entry.record_id,
                    from_layer="session",
                    to_layer="project",
                    status="skipped",
                    reason="session has no project scope",
                )
                for entry in working_distilled
            ]
        return MemoryState(
            working=MemoryLayer(
                layer="working",
                raw_entries=working_raw,
                distilled_entries=working_distilled,
                summary=(
                    f"Working memory tracks {len(working_raw)} raw and "
                    f"{len(working_distilled)} distilled entry(s) for batch {batch_cycle}."
                ),
            ),
            session=MemoryLayer(
                layer="session",
                raw_entries=session_raw,
                distilled_entries=session_distilled,
                summary=(
                    f"Session memory holds {len(session_raw)} raw and "
                    f"{len(session_distilled)} distilled citation-backed entry(s)."
                ),
            ),
            project=MemoryLayer(
                layer="project",
                raw_entries=[],
                distilled_entries=project_entries,
                summary=(
                    f"Project memory holds {len(project_entries)} distilled entry(s)."
                    if session is not None and session.project_id is not None
                    else "Project memory remains scaffolded until a project scope is available."
                ),
            ),
            promotions=[*promotions, *project_promotions],
            demotions=[],
            last_updated_at=datetime.now(UTC).isoformat(),
        )

    @staticmethod
    def _context_dict(state: dict[str, object]) -> dict[str, object]:
        raw = state.get("context")
        return dict(raw) if isinstance(raw, dict) else {}

    @staticmethod
    def _dict_list(raw: object) -> list[dict[str, object]]:
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    @staticmethod
    def _batch_cycle(state: dict[str, object]) -> int:
        batch_raw = state.get("batch")
        if isinstance(batch_raw, dict):
            cycle = batch_raw.get("cycle")
            if isinstance(cycle, int):
                return cycle
        return 0

    def _build_working_raw_entries(
        self, *, state: dict[str, object], batch_cycle: int
    ) -> list[ContextRecord]:
        records = self._dict_list(state.get("execution_records"))
        matched = [record for record in records if record.get("batch_cycle") == batch_cycle]
        entries: list[ContextRecord] = []
        for record in matched:
            trace_id = str(record.get("id") or "")
            label = str(record.get("command_or_action") or record.get("task_node_id") or "workflow")
            entries.append(
                ContextRecord(
                    record_id=f"memory:working:raw:{trace_id}",
                    title=label,
                    summary=str(
                        record.get("summary") or record.get("status") or "execution recorded"
                    ),
                    kind="working_raw",
                    citations=[
                        CitationPointer(
                            source_kind="execution_record",
                            source_id=trace_id,
                            label=label,
                            trace_id=trace_id,
                            task_node_id=(
                                str(record.get("task_node_id"))
                                if isinstance(record.get("task_node_id"), str)
                                else None
                            ),
                        )
                    ],
                    metadata={"batch_cycle": batch_cycle, "status": record.get("status")},
                )
            )
        return entries

    def _build_working_distilled_entries(
        self, working_raw: list[ContextRecord], retrieval: RetrievalState
    ) -> list[ContextRecord]:
        entries: list[ContextRecord] = []
        for raw_entry in working_raw:
            entries.append(
                ContextRecord(
                    record_id=raw_entry.record_id.replace(":raw:", ":distilled:"),
                    title=f"Distilled {raw_entry.title}",
                    summary=f"Distilled from {raw_entry.title}: {raw_entry.summary}",
                    kind="working_distilled",
                    citations=list(raw_entry.citations),
                    metadata={"source_record_id": raw_entry.record_id},
                )
            )
        if retrieval.session_local.items:
            first_item = retrieval.session_local.items[0]
            entries.append(
                ContextRecord(
                    record_id="memory:working:distilled:retrieval-bridge",
                    title="Retrieved session bridge",
                    summary=(
                        "Promote the most relevant session-local retrieval evidence into the "
                        "working set for model-facing context."
                    ),
                    kind="retrieval_bridge",
                    citations=list(first_item.citations),
                    metadata={"source_record_id": first_item.record_id},
                )
            )
        return self._merge_entries([], entries)

    @staticmethod
    def _merge_entries(
        existing: list[ContextRecord], incoming: list[ContextRecord]
    ) -> list[ContextRecord]:
        merged: dict[str, ContextRecord] = {entry.record_id: entry for entry in existing}
        for entry in incoming:
            merged[entry.record_id] = entry
        return list(merged.values())
