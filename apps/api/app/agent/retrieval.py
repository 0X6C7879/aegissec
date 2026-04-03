from __future__ import annotations

from app.agent.context_models import CitationPointer, ContextRecord, RetrievalPack, RetrievalState
from app.agent.memory_recall import select_relevant_memory_entries
from app.db.models import Session, TaskNode, WorkflowRun
from app.db.repositories import RunLogRepository


class RetrievalPipeline:
    def __init__(self, *, run_log_repository: RunLogRepository) -> None:
        self._run_log_repository = run_log_repository

    def build(
        self,
        *,
        run: WorkflowRun,
        session: Session | None,
        state: dict[str, object],
        tasks: list[TaskNode],
    ) -> RetrievalState:
        return RetrievalState(
            session_local=self._build_session_local_pack(run=run, state=state),
            project=self._build_project_pack(session=session, state=state, tasks=tasks),
            capability=self._build_capability_pack(state=state),
        )

    def _build_session_local_pack(
        self, *, run: WorkflowRun, state: dict[str, object]
    ) -> RetrievalPack:
        items: list[ContextRecord] = []
        records = self._dict_list(state.get("archived_execution_records")) + self._dict_list(
            state.get("execution_records")
        )
        for record in records[-4:]:
            trace_id = str(record.get("id") or "")
            task_name = str(
                record.get("task_node_id") or record.get("command_or_action") or "workflow"
            )
            summary = str(record.get("summary") or record.get("status") or "recorded")
            items.append(
                ContextRecord(
                    record_id=f"session-record:{trace_id}",
                    title=f"Execution {task_name}",
                    summary=summary,
                    kind="execution_record",
                    citations=[
                        CitationPointer(
                            source_kind="execution_record",
                            source_id=trace_id,
                            label=str(record.get("command_or_action") or task_name),
                            trace_id=trace_id,
                            task_node_id=(
                                str(record.get("task_node_id"))
                                if isinstance(record.get("task_node_id"), str)
                                else None
                            ),
                        )
                    ],
                    metadata={
                        "status": record.get("status"),
                        "batch_cycle": record.get("batch_cycle"),
                    },
                )
            )
        findings = self._dict_list(state.get("findings"))
        for finding in findings[-3:]:
            finding_id = str(finding.get("id") or "finding")
            trace_id = str(finding.get("trace_id") or "")
            items.append(
                ContextRecord(
                    record_id=f"session-finding:{finding_id}",
                    title=str(finding.get("title") or finding_id),
                    summary=str(finding.get("summary") or "Finding captured in session state."),
                    kind="finding",
                    citations=[
                        CitationPointer(
                            source_kind="finding",
                            source_id=finding_id,
                            label=str(finding.get("task") or finding_id),
                            trace_id=trace_id or None,
                        )
                    ],
                    metadata={"confidence": finding.get("confidence")},
                )
            )
        logs = self._run_log_repository.list_logs(
            session_id=run.session_id, limit=3, sort_order="desc"
        )
        for log in logs:
            items.append(
                ContextRecord(
                    record_id=f"session-log:{log.id}",
                    title=log.event_type,
                    summary=log.message,
                    kind="run_log",
                    citations=[
                        CitationPointer(
                            source_kind="run_log",
                            source_id=log.id,
                            label=log.event_type,
                        )
                    ],
                    metadata={"source": log.source, "level": log.level},
                )
            )
        if not items:
            return RetrievalPack.empty(
                scope="session_local",
                status="empty",
                summary="Session-local retrieval found no prior evidence yet.",
            )
        return RetrievalPack(
            pack_id=f"retrieval:session_local:{run.id}",
            scope="session_local",
            status="ready",
            summary=(
                f"Session-local retrieval assembled {len(items)} item(s) with "
                f"{sum(item.citation_count for item in items)} citation(s)."
            ),
            items=items,
        )

    def _build_project_pack(
        self,
        *,
        session: Session | None,
        state: dict[str, object],
        tasks: list[TaskNode],
    ) -> RetrievalPack:
        if session is None or session.project_id is None:
            return RetrievalPack.empty(
                scope="project",
                status="scaffolded",
                summary=(
                    "Project retrieval scaffolded because the session is not linked to a project."
                ),
            )
        current_task = " ".join(
            task.name for task in tasks if task.status.value in {"ready", "in_progress"}
        )
        recent_tools = [
            str(record.get("command_or_action") or "")
            for record in (
                self._dict_list(state.get("archived_execution_records"))
                + self._dict_list(state.get("execution_records"))
            )[-6:]
            if str(record.get("command_or_action") or "").strip()
        ]
        already_surfaced = self._already_surfaced_entry_ids(state)
        entries = select_relevant_memory_entries(
            session.project_id,
            current_task=current_task,
            recent_tools=recent_tools,
            already_surfaced=already_surfaced,
            top_k=3,
        )
        if not entries:
            return RetrievalPack.empty(
                scope="project",
                status="scaffolded",
                summary="Project retrieval scaffolded but no durable project memory matched.",
            )
        items = [
            ContextRecord(
                record_id=f"project-memory:{entry.entry_id}",
                title=entry.title,
                summary=entry.summary,
                kind="project_memory",
                citations=list(entry.citations),
                metadata={
                    "memory_entry_id": entry.entry_id,
                    "filename": entry.filename,
                    "tags": list(entry.tags),
                    "updated_at": entry.updated_at,
                },
            )
            for entry in entries
        ]
        return RetrievalPack(
            pack_id=f"retrieval:project:{session.project_id}",
            scope="project",
            status="ready",
            summary=(
                f"Project retrieval selected {len(items)} relevant durable memory entry(s) "
                f"for the current task bias."
            ),
            items=items,
        )

    def _build_capability_pack(self, *, state: dict[str, object]) -> RetrievalPack:
        skill_snapshot = self._dict_list(state.get("skill_snapshot"))
        mcp_snapshot = self._dict_list(state.get("mcp_snapshot"))
        items: list[ContextRecord] = []
        if skill_snapshot:
            items.append(
                ContextRecord(
                    record_id="capability:skills",
                    title="Skill snapshot",
                    summary=f"Captured {len(skill_snapshot)} enabled skill entries.",
                    kind="capability_snapshot",
                    citations=[
                        CitationPointer(
                            source_kind="capability_snapshot",
                            source_id="skill_snapshot",
                            label="skill_snapshot",
                        )
                    ],
                    metadata={"count": len(skill_snapshot)},
                )
            )
        if mcp_snapshot:
            items.append(
                ContextRecord(
                    record_id="capability:mcp",
                    title="MCP snapshot",
                    summary=f"Captured {len(mcp_snapshot)} enabled MCP server entries.",
                    kind="capability_snapshot",
                    citations=[
                        CitationPointer(
                            source_kind="capability_snapshot",
                            source_id="mcp_snapshot",
                            label="mcp_snapshot",
                        )
                    ],
                    metadata={"count": len(mcp_snapshot)},
                )
            )
        if not items:
            return RetrievalPack.empty(
                scope="capability",
                status="scaffolded",
                summary="Capability retrieval scaffolded but no snapshot was available.",
            )
        return RetrievalPack(
            pack_id="retrieval:capability",
            scope="capability",
            status="ready",
            summary=(
                f"Capability retrieval reused {len(items)} snapshot item(s) from workflow state."
            ),
            items=items,
        )

    @staticmethod
    def _dict_list(raw: object) -> list[dict[str, object]]:
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    def _already_surfaced_entry_ids(self, state: dict[str, object]) -> set[str]:
        context = state.get("context")
        if not isinstance(context, dict):
            return set()
        surfaced: set[str] = set()
        for item in self._dict_list(
            self._nested_list(context, "retrieval", "project", "items")
        ) + self._dict_list(self._nested_list(context, "memory", "project", "distilled_entries")):
            metadata = item.get("metadata")
            if isinstance(metadata, dict):
                entry_id = metadata.get("memory_entry_id")
                if isinstance(entry_id, str) and entry_id:
                    surfaced.add(entry_id)
        return surfaced

    @staticmethod
    def _nested_list(container: dict[str, object], *keys: str) -> object:
        current: object = container
        for key in keys:
            if not isinstance(current, dict):
                return []
            current = current.get(key)
        return current
