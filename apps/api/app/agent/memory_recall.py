from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.agent.memory_store import (
    MemoryEntry,
    MemoryManifestEntry,
    load_memory_manifest,
    read_memory_entry,
)
from app.agent.recall_policy import RecallPolicy
from app.agent.retrieval_manifest import RetrievalManifestSource, manifest_source_from_memory


def select_relevant_memory_entries(
    project_id: str,
    *,
    current_task: str = "",
    recent_tools: list[str] | None = None,
    already_surfaced: set[str] | None = None,
    recall_policy: RecallPolicy | None = None,
    top_k: int | None = None,
    base_dir: Path | None = None,
) -> list[MemoryEntry]:
    manifest = load_memory_manifest(project_id, base_dir=base_dir)
    policy = recall_policy or RecallPolicy()
    limit = top_k if isinstance(top_k, int) else policy.top_k
    if not manifest or limit <= 0:
        return []
    surfaced = already_surfaced or set()
    manifest_sources = [manifest_source_from_memory(entry) for entry in manifest]
    ranked = sorted(
        manifest_sources,
        key=lambda source: _score_entry(
            source,
            current_task=current_task,
            recent_tools=recent_tools or [],
            already_surfaced=surfaced,
            recall_policy=policy,
        ),
        reverse=True,
    )
    selected = [
        source
        for source in ranked
        if _score_entry(
            source,
            current_task=current_task,
            recent_tools=recent_tools or [],
            already_surfaced=surfaced,
            recall_policy=policy,
        )
        > 0
    ][:limit]
    if len(selected) < limit:
        seen = {source.source_id for source in selected}
        for source in ranked:
            if source.source_id in seen:
                continue
            selected.append(source)
            seen.add(source.source_id)
            if len(selected) >= limit:
                break
    return [
        read_memory_entry(
            project_id,
            filename=str(source.metadata.get("filename") or ""),
            base_dir=base_dir,
        )
        for source in selected[:limit]
    ]


def rank_memory_manifest_sources(
    manifest: list[MemoryManifestEntry],
    *,
    current_task: str,
    recent_tools: list[str],
    already_surfaced: set[str],
    recall_policy: RecallPolicy,
) -> list[dict[str, object]]:
    ranked_sources = sorted(
        [manifest_source_from_memory(entry) for entry in manifest],
        key=lambda source: _score_entry(
            source,
            current_task=current_task,
            recent_tools=recent_tools,
            already_surfaced=already_surfaced,
            recall_policy=recall_policy,
        ),
        reverse=True,
    )
    result: list[dict[str, object]] = []
    for source in ranked_sources:
        result.append(
            {
                **source.to_state(),
                "score": _score_entry(
                    source,
                    current_task=current_task,
                    recent_tools=recent_tools,
                    already_surfaced=already_surfaced,
                    recall_policy=recall_policy,
                ),
            }
        )
    return result


def _score_entry(
    source: RetrievalManifestSource,
    *,
    current_task: str,
    recent_tools: list[str],
    already_surfaced: set[str],
    recall_policy: RecallPolicy,
) -> float:
    task_terms = _terms(current_task)
    recent_tool_terms = {term for tool in recent_tools for term in _terms(tool)}
    tags_raw = source.metadata.get("tags")
    tags = [tag for tag in tags_raw if isinstance(tag, str)] if isinstance(tags_raw, list) else []
    entry_terms = (
        _terms(source.title)
        | _terms(source.summary)
        | {term for tag in tags for term in _terms(tag)}
    )
    source_labels_raw = source.metadata.get("source_labels")
    source_labels = (
        [label for label in source_labels_raw if isinstance(label, str)]
        if isinstance(source_labels_raw, list)
        else []
    )
    source_terms = {term for label in source_labels for term in _terms(label)}
    task_overlap = len(task_terms & (entry_terms | source_terms))
    recent_tool_overlap = len(recent_tool_terms & (entry_terms | source_terms))
    recency_bonus = _recency_bonus(str(source.metadata.get("updated_at") or ""))
    surfaced_penalty = (
        recall_policy.already_surfaced_penalty if source.source_id in already_surfaced else 0.0
    ) + (source.surfaced_count * 0.35)
    compact_bonus = (
        recall_policy.compact_boundary_bias
        if any("compact" in label for label in source_labels)
        else 0.0
    )
    return (
        (task_overlap * recall_policy.task_match_bias)
        + (recent_tool_overlap * recall_policy.recent_tool_bias)
        + (recency_bonus * recall_policy.freshness_bias)
        + float(source.recall_weight)
        + compact_bonus
        - surfaced_penalty
    )


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
