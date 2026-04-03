from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.agent.context_models import CitationPointer
from app.agent.memory_files import ensure_memory_dir, entry_path, manifest_path, slugify

_METADATA_START = "<!-- aegissec-memory-metadata"
_METADATA_END = "-->"
_MANIFEST_START = "<!-- aegissec-memory-manifest"


@dataclass(frozen=True)
class MemoryManifestEntry:
    entry_id: str
    title: str
    summary: str
    tags: tuple[str, ...]
    updated_at: str
    filename: str
    source_labels: tuple[str, ...] = ()
    scope: str = "project"
    source_trace: str | None = None
    recall_weight: float = 1.0
    surfacing_history: tuple[dict[str, object], ...] = ()

    def to_state(self) -> dict[str, object]:
        return {
            "entry_id": self.entry_id,
            "title": self.title,
            "summary": self.summary,
            "tags": list(self.tags),
            "updated_at": self.updated_at,
            "filename": self.filename,
            "source_labels": list(self.source_labels),
            "scope": self.scope,
            "source_trace": self.source_trace,
            "recall_weight": self.recall_weight,
            "surfacing_history": [dict(item) for item in self.surfacing_history],
        }

    @classmethod
    def from_state(cls, raw: object) -> MemoryManifestEntry | None:
        if not isinstance(raw, dict):
            return None
        entry_id = raw.get("entry_id")
        title = raw.get("title")
        summary = raw.get("summary")
        updated_at = raw.get("updated_at")
        filename = raw.get("filename")
        if not all(
            isinstance(value, str) and value
            for value in (entry_id, title, summary, updated_at, filename)
        ):
            return None
        normalized_entry_id = str(entry_id)
        normalized_title = str(title)
        normalized_summary = str(summary)
        normalized_updated_at = str(updated_at)
        normalized_filename = str(filename)
        tags_raw = raw.get("tags")
        source_labels_raw = raw.get("source_labels")
        surfacing_history_raw = raw.get("surfacing_history")
        recall_weight_raw = raw.get("recall_weight")
        return cls(
            entry_id=normalized_entry_id,
            title=normalized_title,
            summary=normalized_summary,
            tags=(
                tuple(item for item in tags_raw if isinstance(item, str))
                if isinstance(tags_raw, list)
                else ()
            ),
            updated_at=normalized_updated_at,
            filename=normalized_filename,
            source_labels=(
                tuple(item for item in source_labels_raw if isinstance(item, str))
                if isinstance(source_labels_raw, list)
                else ()
            ),
            scope=(str(raw.get("scope") or "project") or "project"),
            source_trace=(
                str(raw.get("source_trace"))
                if isinstance(raw.get("source_trace"), str) and str(raw.get("source_trace"))
                else None
            ),
            recall_weight=(
                float(recall_weight_raw) if isinstance(recall_weight_raw, int | float) else 1.0
            ),
            surfacing_history=(
                tuple(item for item in surfacing_history_raw if isinstance(item, dict))
                if isinstance(surfacing_history_raw, list)
                else ()
            ),
        )


@dataclass(frozen=True)
class MemoryEntry:
    entry_id: str
    title: str
    summary: str
    body: str
    tags: tuple[str, ...]
    citations: tuple[CitationPointer, ...]
    updated_at: str
    filename: str
    scope: str = "project"
    source_trace: str | None = None
    recall_weight: float = 1.0
    surfacing_history: tuple[dict[str, object], ...] = ()

    def to_manifest_entry(self) -> MemoryManifestEntry:
        source_labels = tuple(citation.label for citation in self.citations if citation.label)
        return MemoryManifestEntry(
            entry_id=self.entry_id,
            title=self.title,
            summary=self.summary,
            tags=self.tags,
            updated_at=self.updated_at,
            filename=self.filename,
            source_labels=source_labels,
            scope=self.scope,
            source_trace=self.source_trace,
            recall_weight=self.recall_weight,
            surfacing_history=self.surfacing_history,
        )


def load_memory_manifest(
    project_id: str, *, base_dir: Path | None = None
) -> list[MemoryManifestEntry]:
    path = manifest_path(project_id, base_dir=base_dir)
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    metadata = _parse_embedded_json(raw, marker=_MANIFEST_START)
    entries_raw = metadata.get("entries") if isinstance(metadata, dict) else None
    if not isinstance(entries_raw, list):
        return []
    entries: list[MemoryManifestEntry] = []
    for item in entries_raw:
        parsed = MemoryManifestEntry.from_state(item)
        if parsed is not None:
            entries.append(parsed)
    return entries


def write_memory_entry(
    project_id: str,
    *,
    title: str,
    summary: str,
    body: str,
    tags: list[str] | tuple[str, ...],
    citations: list[CitationPointer] | tuple[CitationPointer, ...],
    updated_at: str | None = None,
    entry_id: str | None = None,
    scope: str = "project",
    source_trace: str | None = None,
    recall_weight: float = 1.0,
    surfacing_history: list[dict[str, object]] | tuple[dict[str, object], ...] = (),
    base_dir: Path | None = None,
) -> MemoryEntry:
    ensure_memory_dir(project_id, base_dir=base_dir)
    normalized_updated_at = updated_at or datetime.now(UTC).isoformat()
    normalized_entry_id = entry_id or f"{slugify(title)}-{slugify(summary)[:24]}"
    filename = entry_path(project_id, normalized_entry_id, base_dir=base_dir).name
    entry = MemoryEntry(
        entry_id=normalized_entry_id,
        title=title.strip() or normalized_entry_id,
        summary=summary.strip() or "No summary provided.",
        body=body.strip() or "No body provided.",
        tags=_normalize_tags(tags),
        citations=tuple(citations),
        updated_at=normalized_updated_at,
        filename=filename,
        scope=scope,
        source_trace=source_trace,
        recall_weight=float(recall_weight),
        surfacing_history=_normalize_surfacing_history(surfacing_history),
    )
    path = entry_path(project_id, normalized_entry_id, base_dir=base_dir)
    path.write_text(_render_memory_entry(entry), encoding="utf-8")
    manifest_entries = load_memory_manifest(project_id, base_dir=base_dir)
    manifest_index = {item.entry_id: item for item in manifest_entries}
    manifest_index[entry.entry_id] = entry.to_manifest_entry()
    update_memory_manifest(project_id, entries=list(manifest_index.values()), base_dir=base_dir)
    return entry


def update_memory_manifest(
    project_id: str,
    *,
    entries: list[MemoryManifestEntry],
    base_dir: Path | None = None,
) -> Path:
    ensure_memory_dir(project_id, base_dir=base_dir)
    path = manifest_path(project_id, base_dir=base_dir)
    ordered_entries = sorted(
        entries, key=lambda item: (item.updated_at, item.entry_id), reverse=True
    )
    path.write_text(
        _render_manifest(project_id=project_id, entries=ordered_entries), encoding="utf-8"
    )
    return path


def read_memory_entry(
    project_id: str,
    *,
    entry_id: str | None = None,
    filename: str | None = None,
    base_dir: Path | None = None,
) -> MemoryEntry:
    if entry_id is None and filename is None:
        raise ValueError("entry_id or filename is required")
    path = (
        entry_path(project_id, entry_id, base_dir=base_dir)
        if entry_id is not None
        else ensure_memory_dir(project_id, base_dir=base_dir) / "entries" / str(filename)
    )
    raw = path.read_text(encoding="utf-8")
    metadata = _parse_embedded_json(raw, marker=_METADATA_START)
    citations_raw = metadata.get("citations") if isinstance(metadata, dict) else []
    citations: list[CitationPointer] = []
    if isinstance(citations_raw, list):
        for item in citations_raw:
            parsed = CitationPointer.from_state(item)
            if parsed is not None:
                citations.append(parsed)
    recall_weight_raw = metadata.get("recall_weight")
    surfacing_history_raw = metadata.get("surfacing_history")
    return MemoryEntry(
        entry_id=str(metadata.get("entry_id") or entry_id or path.stem),
        title=str(metadata.get("title") or path.stem),
        summary=str(metadata.get("summary") or ""),
        body=str(metadata.get("body") or ""),
        tags=_normalize_tags(metadata.get("tags") if isinstance(metadata, dict) else ()),
        citations=tuple(citations),
        updated_at=str(metadata.get("updated_at") or ""),
        filename=path.name,
        scope=str(metadata.get("scope") or "project"),
        source_trace=(
            str(metadata.get("source_trace"))
            if isinstance(metadata.get("source_trace"), str) and str(metadata.get("source_trace"))
            else None
        ),
        recall_weight=(
            float(recall_weight_raw) if isinstance(recall_weight_raw, int | float) else 1.0
        ),
        surfacing_history=(
            tuple(item for item in surfacing_history_raw if isinstance(item, dict))
            if isinstance(surfacing_history_raw, list)
            else ()
        ),
    )


def record_memory_entry_surfaced(
    project_id: str,
    *,
    entry_id: str,
    scope: str,
    source_trace: str | None,
    surfaced_at: str | None = None,
    source_pack: str | None = None,
    base_dir: Path | None = None,
) -> MemoryManifestEntry | None:
    manifest = load_memory_manifest(project_id, base_dir=base_dir)
    manifest_index = {item.entry_id: item for item in manifest}
    existing = manifest_index.get(entry_id)
    if existing is None:
        return None
    event = {
        "scope": scope,
        "source_trace": source_trace,
        "source_pack": source_pack or "project",
        "surfaced_at": surfaced_at or datetime.now(UTC).isoformat(),
    }
    history = [*existing.surfacing_history, event][-8:]
    updated = MemoryManifestEntry(
        entry_id=existing.entry_id,
        title=existing.title,
        summary=existing.summary,
        tags=existing.tags,
        updated_at=existing.updated_at,
        filename=existing.filename,
        source_labels=existing.source_labels,
        scope=existing.scope,
        source_trace=existing.source_trace,
        recall_weight=existing.recall_weight,
        surfacing_history=_normalize_surfacing_history(history),
    )
    manifest_index[entry_id] = updated
    update_memory_manifest(project_id, entries=list(manifest_index.values()), base_dir=base_dir)
    return updated


def _parse_embedded_json(content: str, *, marker: str) -> dict[str, object]:
    if not content.startswith(marker):
        return {}
    end_index = content.find(_METADATA_END)
    if end_index == -1:
        return {}
    payload = content[len(marker) : end_index].strip()
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _render_memory_entry(entry: MemoryEntry) -> str:
    metadata = {
        "entry_id": entry.entry_id,
        "title": entry.title,
        "summary": entry.summary,
        "body": entry.body,
        "tags": list(entry.tags),
        "citations": [citation.to_state() for citation in entry.citations],
        "updated_at": entry.updated_at,
        "filename": entry.filename,
        "scope": entry.scope,
        "source_trace": entry.source_trace,
        "recall_weight": entry.recall_weight,
        "surfacing_history": [dict(item) for item in entry.surfacing_history],
    }
    source_lines = [
        f"- {citation.source_kind}:{citation.source_id} ({citation.label})"
        for citation in entry.citations
    ] or ["- No citations recorded."]
    tag_lines = [f"- {tag}" for tag in entry.tags] or ["- untagged"]
    metadata_block = json.dumps(metadata, ensure_ascii=False, indent=2)
    return "\n".join(
        [
            f"{_METADATA_START}\n{metadata_block}\n{_METADATA_END}",
            f"# {entry.title}",
            "",
            "## Summary",
            entry.summary,
            "",
            "## Tags",
            *tag_lines,
            "",
            "## Sources",
            *source_lines,
            "",
            "## Updated At",
            entry.updated_at,
            "",
            "## Recall Metadata",
            f"Scope: {entry.scope}",
            f"Source trace: {entry.source_trace or 'n/a'}",
            f"Recall weight: {entry.recall_weight:.2f}",
            f"Surfaced count: {len(entry.surfacing_history)}",
            "",
            "## Body",
            entry.body,
            "",
        ]
    )


def _render_manifest(*, project_id: str, entries: list[MemoryManifestEntry]) -> str:
    metadata = {
        "project_id": project_id,
        "entries": [entry.to_state() for entry in entries],
        "updated_at": datetime.now(UTC).isoformat(),
    }
    visible_entries = [
        (
            f"- [{entry.title}](entries/{entry.filename}) — {entry.summary} "
            f"(scope: {entry.scope}; tags: {', '.join(entry.tags) or 'untagged'}; "
            f"weight: {entry.recall_weight:.2f}; surfaced: {len(entry.surfacing_history)}; "
            f"updated: {entry.updated_at})"
        )
        for entry in entries
    ] or ["- No durable project memory has been recorded yet."]
    metadata_block = json.dumps(metadata, ensure_ascii=False, indent=2)
    return "\n".join(
        [
            f"{_MANIFEST_START}\n{metadata_block}\n{_METADATA_END}",
            "# Project Memory Index",
            "",
            "This index keeps only lightweight summaries and links to detailed memory entries.",
            "",
            *visible_entries,
            "",
        ]
    )


def _normalize_tags(raw_tags: object) -> tuple[str, ...]:
    if not isinstance(raw_tags, list | tuple):
        return ()
    normalized = []
    for item in raw_tags:
        if not isinstance(item, str):
            continue
        cleaned = item.strip().lower()
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return tuple(normalized)


def _normalize_surfacing_history(raw_history: object) -> tuple[dict[str, object], ...]:
    if not isinstance(raw_history, list | tuple):
        return ()
    return tuple(item for item in raw_history if isinstance(item, dict))[:8]
