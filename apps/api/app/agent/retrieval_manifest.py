from __future__ import annotations

from dataclasses import dataclass, field

from app.agent.memory_store import MemoryManifestEntry


@dataclass(frozen=True)
class RetrievalManifestSource:
    source_id: str
    scope: str
    kind: str
    title: str
    summary: str
    source_trace: str | None = None
    recall_weight: float = 1.0
    surfaced_count: int = 0
    metadata: dict[str, object] = field(default_factory=dict)

    def to_state(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "scope": self.scope,
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
            "source_trace": self.source_trace,
            "recall_weight": self.recall_weight,
            "surfaced_count": self.surfaced_count,
            "metadata": dict(self.metadata),
        }


def manifest_source_from_memory(entry: MemoryManifestEntry) -> RetrievalManifestSource:
    return RetrievalManifestSource(
        source_id=entry.entry_id,
        scope=entry.scope,
        kind="durable_memory",
        title=entry.title,
        summary=entry.summary,
        source_trace=entry.source_trace,
        recall_weight=entry.recall_weight,
        surfaced_count=len(entry.surfacing_history),
        metadata={
            "filename": entry.filename,
            "tags": list(entry.tags),
            "source_labels": list(entry.source_labels),
            "updated_at": entry.updated_at,
            "surfacing_history": [dict(item) for item in entry.surfacing_history],
        },
    )
