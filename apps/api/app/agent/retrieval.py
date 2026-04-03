from __future__ import annotations

from datetime import UTC, datetime

from app.agent.context_models import CitationPointer, ContextRecord, RetrievalPack, RetrievalState
from app.agent.memory_recall import rank_memory_manifest_sources, select_relevant_memory_entries
from app.agent.memory_store import load_memory_manifest, record_memory_entry_surfaced
from app.agent.recall_policy import RecallPolicy
from app.agent.transcript_runtime import TranscriptRuntimeService
from app.db.models import Session, TaskNode, WorkflowRun
from app.db.repositories import RunLogRepository


class RetrievalPipeline:
    def __init__(self, *, run_log_repository: RunLogRepository) -> None:
        self._run_log_repository = run_log_repository
        self._transcript_runtime = TranscriptRuntimeService()

    def build(
        self,
        *,
        run: WorkflowRun,
        session: Session | None,
        state: dict[str, object],
        tasks: list[TaskNode],
    ) -> RetrievalState:
        recall_policy = RecallPolicy.from_state(state.get("recall_policy"))
        existing_manifest = self._dict(state.get("retrieval_manifest"))
        existing_project_manifest = self._dict(existing_manifest.get("project"))
        project_manifest = self._build_project_manifest(
            session=session,
            state=state,
            tasks=tasks,
            recall_policy=recall_policy,
        )
        if (
            self._int(project_manifest.get("source_count"), default=0) == 0
            and isinstance(existing_project_manifest.get("sources"), list)
            and bool(existing_project_manifest.get("sources"))
        ):
            project_manifest = existing_project_manifest
        retrieval_manifest = {
            "policy": recall_policy.to_state(),
            "session_local": self._build_session_manifest(run=run, state=state),
            "project": project_manifest,
            "capability": self._build_capability_manifest(state=state),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        state["retrieval_manifest"] = retrieval_manifest
        return RetrievalState(
            session_local=self._build_session_local_pack(run=run, state=state),
            project=self._build_project_pack(
                session=session,
                state=state,
                tasks=tasks,
                recall_policy=recall_policy,
                manifest=self._dict(retrieval_manifest.get("project")),
            ),
            capability=self._build_capability_pack(state=state),
        )

    def _build_session_manifest(
        self, *, run: WorkflowRun, state: dict[str, object]
    ) -> dict[str, object]:
        recent_deltas = self._transcript_runtime.recent_deltas(state, limit=6)
        execution_records = self._dict_list(
            state.get("archived_execution_records")
        ) + self._dict_list(state.get("execution_records"))
        findings = self._dict_list(state.get("findings"))
        return {
            "scope": "session_derived",
            "source_count": len(recent_deltas) + len(execution_records[-4:]) + len(findings[-3:]),
            "sources": [
                {
                    "source_id": str(delta.get("delta_id") or ""),
                    "scope": "session_derived",
                    "kind": "runtime_transcript",
                    "summary": self._transcript_preview(delta),
                }
                for delta in recent_deltas
            ]
            + [
                {
                    "source_id": str(record.get("id") or ""),
                    "scope": "session_derived",
                    "kind": "execution_record",
                    "summary": str(record.get("summary") or record.get("status") or "recorded"),
                }
                for record in execution_records[-4:]
            ]
            + [
                {
                    "source_id": str(finding.get("id") or ""),
                    "scope": "session_derived",
                    "kind": "finding",
                    "summary": str(finding.get("summary") or "finding"),
                }
                for finding in findings[-3:]
            ],
        }

    def _build_session_local_pack(
        self, *, run: WorkflowRun, state: dict[str, object]
    ) -> RetrievalPack:
        items: list[ContextRecord] = []
        transcript_deltas = self._transcript_runtime.recent_deltas(state, limit=4)
        for delta in transcript_deltas:
            delta_id = str(delta.get("delta_id") or "")
            metadata = self._dict(delta.get("metadata"))
            summary = self._transcript_preview(delta)
            if not summary:
                continue
            trace_id = str(metadata.get("trace_id") or "")
            task_name = str(metadata.get("task_name") or metadata.get("tool_name") or "workflow")
            items.append(
                ContextRecord(
                    record_id=f"transcript-delta:{delta_id}",
                    title=f"Transcript {task_name}",
                    summary=summary,
                    kind="transcript_delta",
                    citations=[
                        CitationPointer(
                            source_kind="transcript_delta",
                            source_id=delta_id,
                            label=task_name,
                            trace_id=trace_id or None,
                            task_node_id=(
                                str(metadata.get("task_id"))
                                if isinstance(metadata.get("task_id"), str)
                                else None
                            ),
                        )
                    ],
                    metadata={"status": metadata.get("status"), "trace_id": trace_id},
                )
            )
        for event in self._transcript_runtime.recent_compact_events(state, limit=2):
            event_id = str(event.get("event_id") or "")
            summary = str(event.get("summary") or "")
            if not summary:
                continue
            items.append(
                ContextRecord(
                    record_id=f"compact-event:{event_id}",
                    title="Compact continuity",
                    summary=summary,
                    kind="compact_event",
                    citations=[
                        CitationPointer(
                            source_kind="compact_event",
                            source_id=event_id,
                            label=str(event.get("boundary_marker") or "compact_boundary"),
                        )
                    ],
                    metadata={"boundary_marker": event.get("boundary_marker")},
                )
            )
        for event in self._transcript_runtime.recent_reinjection_events(state, limit=2):
            event_id = str(event.get("event_id") or "")
            summary = str(event.get("summary") or "")
            if not summary:
                continue
            items.append(
                ContextRecord(
                    record_id=f"reinjection-event:{event_id}",
                    title="Reinjection continuity",
                    summary=summary,
                    kind="reinjection_event",
                    citations=[
                        CitationPointer(
                            source_kind="reinjection_event",
                            source_id=event_id,
                            label=str(event.get("boundary_marker") or "reinjection"),
                        )
                    ],
                    metadata={"boundary_marker": event.get("boundary_marker")},
                )
            )
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
        recall_policy: RecallPolicy,
        manifest: dict[str, object],
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
        transcript_recent_tools = [
            str(record.get("command_or_action") or "")
            for record in self._transcript_runtime.recent_tool_result_records(state, limit=6)
            if str(record.get("command_or_action") or "").strip()
        ]
        recent_tools = transcript_recent_tools or [
            str(record.get("command_or_action") or "")
            for record in (
                self._dict_list(state.get("archived_execution_records"))
                + self._dict_list(state.get("execution_records"))
            )[-6:]
            if str(record.get("command_or_action") or "").strip()
        ]
        already_surfaced = self._already_surfaced_entry_ids(state)
        state["surfaced_history_state"] = {
            "already_surfaced": sorted(already_surfaced),
            "policy": recall_policy.to_state(),
        }
        entries = select_relevant_memory_entries(
            session.project_id,
            current_task=current_task,
            recent_tools=recent_tools,
            already_surfaced=already_surfaced,
            recall_policy=recall_policy,
        )
        if not entries:
            return RetrievalPack.empty(
                scope="project",
                status="scaffolded",
                summary="Project retrieval scaffolded but no durable project memory matched.",
            )
        items = []
        for entry in entries:
            items.append(
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
                        "scope": entry.scope,
                        "source_trace": entry.source_trace,
                        "recall_weight": entry.recall_weight,
                        "surfaced_count": len(entry.surfacing_history),
                    },
                )
            )
            record_memory_entry_surfaced(
                session.project_id,
                entry_id=entry.entry_id,
                scope="session_derived",
                source_trace=entry.source_trace,
                source_pack="project",
            )
        return RetrievalPack(
            pack_id=f"retrieval:project:{session.project_id}",
            scope="project",
            status="ready",
            summary=(
                f"Project retrieval selected {len(items)} relevant durable memory entry(s) "
                "from "
                f"{self._int(manifest.get('source_count'), default=0)} "
                "manifest candidate source(s)."
            ),
            items=items,
        )

    def _build_project_manifest(
        self,
        *,
        session: Session | None,
        state: dict[str, object],
        tasks: list[TaskNode],
        recall_policy: RecallPolicy,
    ) -> dict[str, object]:
        if session is None or session.project_id is None:
            return {"scope": "project", "source_count": 0, "sources": []}
        current_task = " ".join(
            task.name for task in tasks if task.status.value in {"ready", "in_progress"}
        )
        recent_tools = [
            str(record.get("command_or_action") or "")
            for record in self._transcript_runtime.recent_tool_result_records(state, limit=6)
            if str(record.get("command_or_action") or "").strip()
        ]
        ranked_sources = rank_memory_manifest_sources(
            load_memory_manifest(session.project_id),
            current_task=current_task,
            recent_tools=recent_tools,
            already_surfaced=self._already_surfaced_entry_ids(state),
            recall_policy=recall_policy,
        )
        return {"scope": "project", "source_count": len(ranked_sources), "sources": ranked_sources}

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

    def _build_capability_manifest(self, *, state: dict[str, object]) -> dict[str, object]:
        skill_snapshot = self._dict_list(state.get("skill_snapshot"))
        mcp_snapshot = self._dict_list(state.get("mcp_snapshot"))
        sources: list[dict[str, object]] = []
        if skill_snapshot:
            sources.append(
                {
                    "source_id": "skill_snapshot",
                    "scope": "capability_adjacent",
                    "kind": "capability_snapshot",
                    "summary": f"Captured {len(skill_snapshot)} enabled skill entries.",
                }
            )
        if mcp_snapshot:
            sources.append(
                {
                    "source_id": "mcp_snapshot",
                    "scope": "capability_adjacent",
                    "kind": "capability_snapshot",
                    "summary": f"Captured {len(mcp_snapshot)} enabled MCP server entries.",
                }
            )
        return {"scope": "capability_adjacent", "source_count": len(sources), "sources": sources}

    @staticmethod
    def _dict_list(raw: object) -> list[dict[str, object]]:
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    @staticmethod
    def _dict(raw: object) -> dict[str, object]:
        if not isinstance(raw, dict):
            return {}
        return {str(key): value for key, value in raw.items()}

    @staticmethod
    def _int(raw: object, *, default: int) -> int:
        return raw if isinstance(raw, int) else default

    def _already_surfaced_entry_ids(self, state: dict[str, object]) -> set[str]:
        context = state.get("context")
        surfaced: set[str] = set()
        if isinstance(context, dict):
            for item in self._dict_list(
                self._nested_list(context, "retrieval", "project", "items")
            ) + self._dict_list(
                self._nested_list(context, "memory", "project", "distilled_entries")
            ):
                record_id = item.get("record_id")
                if isinstance(record_id, str) and record_id:
                    surfaced.add(record_id)
                metadata = item.get("metadata")
                if isinstance(metadata, dict):
                    entry_id = metadata.get("memory_entry_id")
                    if isinstance(entry_id, str) and entry_id:
                        surfaced.add(entry_id)
        surfaced_state = state.get("surfaced_history_state")
        if isinstance(surfaced_state, dict):
            already_surfaced = surfaced_state.get("already_surfaced")
            if isinstance(already_surfaced, list):
                surfaced.update(item for item in already_surfaced if isinstance(item, str))
        return surfaced

    def _transcript_preview(self, delta: dict[str, object]) -> str:
        for key in (
            "tool_result_blocks",
            "tool_error_blocks",
            "compact_boundary_blocks",
            "reinjection_blocks",
            "assistant_blocks",
        ):
            blocks = delta.get(key)
            if not isinstance(blocks, list):
                continue
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                content = str(block.get("content") or "").strip()
                if content:
                    return content
        return ""

    @staticmethod
    def _nested_list(container: dict[str, object], *keys: str) -> object:
        current: object = container
        for key in keys:
            if not isinstance(current, dict):
                return []
            current = current.get(key)
        return current
