from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.agent.memory_store import (
    MemoryEntry,
    MemoryManifestEntry,
    load_memory_manifest,
    read_memory_entry,
)


def select_relevant_memory_entries(
    project_id: str,
    *,
    current_task: str = "",
    recent_tools: list[str] | None = None,
    already_surfaced: set[str] | None = None,
    top_k: int = 3,
    base_dir: Path | None = None,
) -> list[MemoryEntry]:
    manifest = load_memory_manifest(project_id, base_dir=base_dir)
    if not manifest or top_k <= 0:
        return []
    surfaced = already_surfaced or set()
    ranked = sorted(
        manifest,
        key=lambda entry: _score_entry(
            entry,
            current_task=current_task,
            recent_tools=recent_tools or [],
            already_surfaced=surfaced,
        ),
        reverse=True,
    )
    selected = [
        entry
        for entry in ranked
        if _score_entry(
            entry,
            current_task=current_task,
            recent_tools=recent_tools or [],
            already_surfaced=surfaced,
        )
        > 0
    ][:top_k]
    if len(selected) < top_k:
        seen = {entry.entry_id for entry in selected}
        for entry in ranked:
            if entry.entry_id in seen:
                continue
            selected.append(entry)
            seen.add(entry.entry_id)
            if len(selected) >= top_k:
                break
    return [
        read_memory_entry(project_id, filename=entry.filename, base_dir=base_dir)
        for entry in selected[:top_k]
    ]


def _score_entry(
    entry: MemoryManifestEntry,
    *,
    current_task: str,
    recent_tools: list[str],
    already_surfaced: set[str],
) -> float:
    query_terms = _terms(current_task) | {term for tool in recent_tools for term in _terms(tool)}
    entry_terms = (
        _terms(entry.title)
        | _terms(entry.summary)
        | {term for tag in entry.tags for term in _terms(tag)}
    )
    source_terms = {term for label in entry.source_labels for term in _terms(label)}
    overlap = len(query_terms & (entry_terms | source_terms))
    recency_bonus = _recency_bonus(entry.updated_at)
    surfaced_penalty = 4.0 if entry.entry_id in already_surfaced else 0.0
    source_bonus = min(len(entry.source_labels), 3) * 0.15
    return overlap * 2.0 + recency_bonus + source_bonus - surfaced_penalty


def _terms(text: str) -> set[str]:
    return {
        part
        for part in text.lower().replace(".", " ").replace("_", " ").replace(":", " ").split()
        if part
    }


def _recency_bonus(updated_at: str) -> float:
    try:
        updated = datetime.fromisoformat(updated_at)
    except ValueError:
        return 0.0
    age_days = max((datetime.now(UTC) - updated).total_seconds() / 86400.0, 0.0)
    if age_days <= 1:
        return 1.0
    if age_days <= 7:
        return 0.5
    if age_days <= 30:
        return 0.2
    return 0.0
