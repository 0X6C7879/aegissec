from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.agent.context_models import CitationPointer
from app.agent.memory_files import ensure_memory_dir
from app.agent.memory_recall import select_relevant_memory_entries
from app.agent.memory_store import MemoryEntry, record_memory_entry_surfaced, write_memory_entry
from app.agent.recall_policy import RecallPolicy

_MEMORY_CATEGORY_DIRS: tuple[str, ...] = (
    "targets",
    "findings",
    "tactics",
    "playbooks",
    "credentials",
)


@dataclass(slots=True)
class HarnessMemoryContext:
    memory_key: str
    entries: list[MemoryEntry] = field(default_factory=list)
    retrieval_fragment: str = ""
    memory_fragment: str = ""


class HarnessMemoryService:
    def __init__(self, *, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir

    def memory_key_for_session(self, session_id: str, project_id: str | None) -> str:
        normalized_project_id = (project_id or "").strip()
        normalized_session_id = session_id.strip() or "unknown-session"
        return normalized_project_id or normalized_session_id

    def ensure_layout(self, memory_key: str) -> Path:
        memory_dir = ensure_memory_dir(memory_key, base_dir=self._base_dir)
        for directory_name in _MEMORY_CATEGORY_DIRS:
            (memory_dir / directory_name).mkdir(parents=True, exist_ok=True)
        return memory_dir

    def build_context(
        self,
        *,
        session_id: str,
        project_id: str | None,
        current_task: str,
        workflow_phase: str | None,
        session_goal: str | None,
        scenario_type: str | None,
        active_hypotheses: list[str] | None = None,
        recent_entities: list[str] | None = None,
        recent_tools: list[str] | None = None,
        top_k: int = 3,
    ) -> HarnessMemoryContext:
        memory_key = self.memory_key_for_session(session_id, project_id)
        self.ensure_layout(memory_key)
        recall_query = self._build_recall_query(
            current_task=current_task,
            workflow_phase=workflow_phase,
            session_goal=session_goal,
            scenario_type=scenario_type,
            active_hypotheses=active_hypotheses or [],
            recent_entities=recent_entities or [],
        )
        entries = select_relevant_memory_entries(
            memory_key,
            current_task=recall_query,
            recent_tools=recent_tools or [],
            recall_policy=RecallPolicy(top_k=top_k),
            top_k=top_k,
            base_dir=self._base_dir,
        )
        for entry in entries:
            record_memory_entry_surfaced(
                memory_key,
                entry_id=entry.entry_id,
                scope="session",
                source_trace=session_id,
                source_pack="harness",
                base_dir=self._base_dir,
            )
        return HarnessMemoryContext(
            memory_key=memory_key,
            entries=entries,
            retrieval_fragment=self._render_retrieval_fragment(entries),
            memory_fragment=self._render_memory_fragment(entries),
        )

    def write_compact_boundary(
        self,
        *,
        session_state: object,
        title: str,
        summary: str,
        body: str,
        tags: list[str] | None = None,
    ) -> MemoryEntry:
        memory_key = getattr(session_state, "memory_key")
        session_id = getattr(session_state, "session_id")
        self.ensure_layout(memory_key)
        return write_memory_entry(
            memory_key,
            title=title,
            summary=summary,
            body=body,
            tags=tags or ["compact", "harness"],
            citations=[
                CitationPointer(
                    source_kind="session",
                    source_id=session_id,
                    label=f"session:{session_id}",
                )
            ],
            source_trace=session_id,
            base_dir=self._base_dir,
        )

    def _build_recall_query(
        self,
        *,
        current_task: str,
        workflow_phase: str | None,
        session_goal: str | None,
        scenario_type: str | None,
        active_hypotheses: list[str],
        recent_entities: list[str],
    ) -> str:
        parts = [current_task.strip()]
        if session_goal and session_goal.strip():
            parts.append(f"goal: {session_goal.strip()}")
        if workflow_phase and workflow_phase.strip():
            parts.append(f"phase: {workflow_phase.strip()}")
        if scenario_type and scenario_type.strip():
            parts.append(f"scenario: {scenario_type.strip()}")
        if active_hypotheses:
            parts.append("hypotheses: " + ", ".join(item for item in active_hypotheses if item))
        if recent_entities:
            parts.append("entities: " + ", ".join(item for item in recent_entities if item))
        return "\n".join(part for part in parts if part)

    def _render_retrieval_fragment(self, entries: list[MemoryEntry]) -> str:
        if not entries:
            return ""
        lines = ["## Relevant Memory Sources"]
        for entry in entries:
            lines.append(f"- {entry.title} [{entry.entry_id}]")
        return "\n".join(lines)

    def _render_memory_fragment(self, entries: list[MemoryEntry]) -> str:
        if not entries:
            return ""
        blocks = ["## Relevant Memory"]
        for entry in entries:
            summary = entry.summary.strip() or "No summary available."
            body = entry.body.strip()
            tags = f" tags={', '.join(entry.tags)}" if entry.tags else ""
            blocks.append(f"### {entry.title} [{entry.entry_id}]{tags}\n{summary}")
            if body:
                blocks.append(body)
        return "\n\n".join(blocks)
