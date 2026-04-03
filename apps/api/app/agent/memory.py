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
from app.agent.memory_store import write_memory_entry
from app.agent.session_memory import SessionMemoryService
from app.agent.transcript_runtime import TranscriptRuntimeService
from app.db.models import Session


class MemoryManager:
    def __init__(self) -> None:
        self._session_memory = SessionMemoryService()
        self._transcript_runtime = TranscriptRuntimeService()

    def build(
        self,
        *,
        session: Session | None,
        state: dict[str, object],
        retrieval: RetrievalState,
    ) -> MemoryState:
        previous = MemoryState.from_state(self._context_dict(state).get("memory"))
        batch_cycle = self._batch_cycle(state)
        session_summary = self._session_memory.update_session_summary(
            state=state, retrieval=retrieval
        )
        working_raw = self._build_working_raw_entries(state=state, batch_cycle=batch_cycle)
        working_distilled = self._build_working_distilled_entries(working_raw, retrieval)
        session_raw = self._merge_entries(previous.session.raw_entries, working_raw)
        session_distilled = self._merge_entries(
            previous.session.distilled_entries, working_distilled
        )
        if session_summary.summary:
            session_distilled = self._merge_entries(
                session_distilled,
                [
                    self._build_session_summary_record(
                        session_summary.summary, working_raw, retrieval
                    )
                ],
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
        project_entries = list(retrieval.project.items)
        project_promotions: list[MemoryOperation] = []
        persistable_working_distilled = [
            entry for entry in working_distilled if self._should_persist_to_project(entry)
        ]
        if session is not None and session.project_id is not None:
            durable_entry_ids = [
                self._persist_project_memory_entry(
                    project_id=session.project_id,
                    entry=entry,
                    working_raw=working_raw,
                )
                for entry in persistable_working_distilled
            ]
            project_promotions = [
                MemoryOperation(
                    entry_id=entry_id,
                    from_layer="session",
                    to_layer="project",
                    status="promoted",
                )
                for entry_id in durable_entry_ids
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
                for entry in persistable_working_distilled
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
                    session_summary.summary
                    if session_summary.summary
                    else (
                        f"Session memory holds {len(session_raw)} raw and "
                        f"{len(session_distilled)} distilled citation-backed entry(s)."
                    )
                ),
            ),
            project=MemoryLayer(
                layer="project",
                raw_entries=[],
                distilled_entries=project_entries,
                summary=(
                    f"Project memory surfaced {len(project_entries)} relevant durable entry(s)."
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
        transcript_entries: list[ContextRecord] = []
        cycle_id = f"cycle-{batch_cycle}"
        for record in self._transcript_runtime.recent_tool_result_records(state, limit=12):
            if str(record.get("cycle_id") or "") != cycle_id:
                continue
            trace_id = str(record.get("trace_id") or "")
            task_name = str(record.get("task_name") or "workflow")
            transcript_entries.append(
                ContextRecord(
                    record_id=f"memory:working:raw:{trace_id}",
                    title=task_name,
                    summary=f"Transcript runtime {task_name}: {record.get('status') or 'recorded'}",
                    kind="working_raw",
                    citations=[
                        CitationPointer(
                            source_kind="transcript_tool_result",
                            source_id=trace_id,
                            label=task_name,
                            trace_id=trace_id,
                            task_node_id=(
                                str(record.get("task_id"))
                                if isinstance(record.get("task_id"), str)
                                else None
                            ),
                        )
                    ],
                    metadata={"batch_cycle": batch_cycle, "status": record.get("status")},
                )
            )
        if transcript_entries:
            return transcript_entries
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

    def _build_session_summary_record(
        self,
        summary: str,
        working_raw: list[ContextRecord],
        retrieval: RetrievalState,
    ) -> ContextRecord:
        citations = [
            citation
            for entry in (working_raw[:2] + retrieval.session_local.items[:1])
            for citation in entry.citations[:1]
        ]
        return ContextRecord(
            record_id="memory:session:summary",
            title="Session summary",
            summary=summary,
            kind="session_summary",
            citations=citations,
            metadata={"kind": "session_summary"},
        )

    def _persist_project_memory_entry(
        self,
        *,
        project_id: str,
        entry: ContextRecord,
        working_raw: list[ContextRecord],
    ) -> str:
        supporting_summaries = [
            raw.summary for raw in working_raw if raw.record_id != entry.record_id
        ]
        body_parts = [entry.summary]
        if supporting_summaries:
            body_parts.append("Supporting evidence: " + "; ".join(supporting_summaries[:3]))
        stored_entry = write_memory_entry(
            project_id,
            entry_id=entry.record_id,
            title=entry.title,
            summary=entry.summary,
            body="\n\n".join(body_parts),
            tags=self._entry_tags(entry),
            citations=list(entry.citations),
            updated_at=datetime.now(UTC).isoformat(),
        )
        return stored_entry.entry_id

    @staticmethod
    def _entry_tags(entry: ContextRecord) -> list[str]:
        tags = [entry.kind]
        metadata_tags = entry.metadata.get("tags") if isinstance(entry.metadata, dict) else None
        if isinstance(metadata_tags, list):
            tags.extend(item for item in metadata_tags if isinstance(item, str))
        return [tag for index, tag in enumerate(tags) if tag and tag not in tags[:index]]

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
                    metadata={"source_record_id": raw_entry.record_id, "persist_to_project": True},
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
                    metadata={
                        "source_record_id": first_item.record_id,
                        "persist_to_project": False,
                    },
                )
            )
        return self._merge_entries([], entries)

    @staticmethod
    def _should_persist_to_project(entry: ContextRecord) -> bool:
        persist_flag = (
            entry.metadata.get("persist_to_project") if isinstance(entry.metadata, dict) else None
        )
        if isinstance(persist_flag, bool):
            return persist_flag
        return entry.kind == "working_distilled"

    @staticmethod
    def _merge_entries(
        existing: list[ContextRecord], incoming: list[ContextRecord]
    ) -> list[ContextRecord]:
        merged: dict[str, ContextRecord] = {entry.record_id: entry for entry in existing}
        for entry in incoming:
            merged[entry.record_id] = entry
        return list(merged.values())
