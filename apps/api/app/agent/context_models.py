from __future__ import annotations

from dataclasses import dataclass, field


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        return []
    return value


@dataclass(frozen=True)
class CitationPointer:
    source_kind: str
    source_id: str
    label: str
    trace_id: str | None = None
    task_node_id: str | None = None
    artifact_name: str | None = None

    def to_state(self) -> dict[str, object]:
        return {
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "label": self.label,
            "trace_id": self.trace_id,
            "task_node_id": self.task_node_id,
            "artifact_name": self.artifact_name,
        }

    @classmethod
    def from_state(cls, raw: object) -> CitationPointer | None:
        raw_dict = _dict(raw)
        source_kind = _string(raw_dict.get("source_kind"))
        source_id = _string(raw_dict.get("source_id"))
        label = _string(raw_dict.get("label"))
        if source_kind is None or source_id is None or label is None:
            return None
        return cls(
            source_kind=source_kind,
            source_id=source_id,
            label=label,
            trace_id=_string(raw_dict.get("trace_id")),
            task_node_id=_string(raw_dict.get("task_node_id")),
            artifact_name=_string(raw_dict.get("artifact_name")),
        )


@dataclass(frozen=True)
class ContextRecord:
    record_id: str
    title: str
    summary: str
    kind: str
    citations: list[CitationPointer] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def citation_count(self) -> int:
        return len(self.citations)

    def to_state(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "title": self.title,
            "summary": self.summary,
            "kind": self.kind,
            "citation_count": self.citation_count,
            "citations": [citation.to_state() for citation in self.citations],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_state(cls, raw: object) -> ContextRecord | None:
        raw_dict = _dict(raw)
        record_id = _string(raw_dict.get("record_id"))
        title = _string(raw_dict.get("title"))
        summary = _string(raw_dict.get("summary"))
        kind = _string(raw_dict.get("kind"))
        if record_id is None or title is None or summary is None or kind is None:
            return None
        citations_raw = raw_dict.get("citations")
        citations: list[CitationPointer] = []
        if isinstance(citations_raw, list):
            for item in citations_raw:
                parsed = CitationPointer.from_state(item)
                if parsed is not None:
                    citations.append(parsed)
        return cls(
            record_id=record_id,
            title=title,
            summary=summary,
            kind=kind,
            citations=citations,
            metadata=_dict(raw_dict.get("metadata")),
        )


@dataclass(frozen=True)
class RetrievalPack:
    pack_id: str
    scope: str
    status: str
    summary: str
    items: list[ContextRecord] = field(default_factory=list)

    @property
    def source_count(self) -> int:
        return len(self.items)

    @property
    def citation_count(self) -> int:
        return sum(item.citation_count for item in self.items)

    def to_state(self) -> dict[str, object]:
        return {
            "pack_id": self.pack_id,
            "scope": self.scope,
            "status": self.status,
            "summary": self.summary,
            "source_count": self.source_count,
            "citation_count": self.citation_count,
            "items": [item.to_state() for item in self.items],
        }

    @classmethod
    def empty(cls, *, scope: str, status: str, summary: str) -> RetrievalPack:
        return cls(pack_id=f"retrieval:{scope}", scope=scope, status=status, summary=summary)

    @classmethod
    def from_state(cls, raw: object) -> RetrievalPack | None:
        raw_dict = _dict(raw)
        pack_id = _string(raw_dict.get("pack_id"))
        scope = _string(raw_dict.get("scope"))
        status = _string(raw_dict.get("status"))
        summary = _string(raw_dict.get("summary"))
        if pack_id is None or scope is None or status is None or summary is None:
            return None
        items_raw = raw_dict.get("items")
        items: list[ContextRecord] = []
        if isinstance(items_raw, list):
            for item in items_raw:
                parsed = ContextRecord.from_state(item)
                if parsed is not None:
                    items.append(parsed)
        return cls(pack_id=pack_id, scope=scope, status=status, summary=summary, items=items)


@dataclass(frozen=True)
class RetrievalState:
    session_local: RetrievalPack
    project: RetrievalPack
    capability: RetrievalPack

    def to_state(self) -> dict[str, object]:
        return {
            "session_local": self.session_local.to_state(),
            "project": self.project.to_state(),
            "capability": self.capability.to_state(),
            "summary": self.summary,
        }

    @property
    def summary(self) -> str:
        return " | ".join(
            [
                self.session_local.summary,
                self.project.summary,
                self.capability.summary,
            ]
        )

    @classmethod
    def empty(cls) -> RetrievalState:
        return cls(
            session_local=RetrievalPack.empty(
                scope="session_local",
                status="pending",
                summary="Session-local retrieval not built yet.",
            ),
            project=RetrievalPack.empty(
                scope="project",
                status="scaffolded",
                summary="Project retrieval scaffolded but not populated yet.",
            ),
            capability=RetrievalPack.empty(
                scope="capability",
                status="scaffolded",
                summary="Capability retrieval scaffolded but not populated yet.",
            ),
        )

    @classmethod
    def from_state(cls, raw: object) -> RetrievalState:
        raw_dict = _dict(raw)
        session_local = RetrievalPack.from_state(raw_dict.get("session_local"))
        project = RetrievalPack.from_state(raw_dict.get("project"))
        capability = RetrievalPack.from_state(raw_dict.get("capability"))
        if session_local is None or project is None or capability is None:
            return cls.empty()
        return cls(session_local=session_local, project=project, capability=capability)


@dataclass(frozen=True)
class MemoryOperation:
    entry_id: str
    from_layer: str
    to_layer: str
    status: str
    reason: str | None = None

    def to_state(self) -> dict[str, object]:
        return {
            "entry_id": self.entry_id,
            "from_layer": self.from_layer,
            "to_layer": self.to_layer,
            "status": self.status,
            "reason": self.reason,
        }

    @classmethod
    def from_state(cls, raw: object) -> MemoryOperation | None:
        raw_dict = _dict(raw)
        entry_id = _string(raw_dict.get("entry_id"))
        from_layer = _string(raw_dict.get("from_layer"))
        to_layer = _string(raw_dict.get("to_layer"))
        status = _string(raw_dict.get("status"))
        if entry_id is None or from_layer is None or to_layer is None or status is None:
            return None
        return cls(
            entry_id=entry_id,
            from_layer=from_layer,
            to_layer=to_layer,
            status=status,
            reason=_string(raw_dict.get("reason")),
        )


@dataclass(frozen=True)
class MemoryLayer:
    layer: str
    raw_entries: list[ContextRecord] = field(default_factory=list)
    distilled_entries: list[ContextRecord] = field(default_factory=list)
    summary: str = ""

    def to_state(self) -> dict[str, object]:
        return {
            "layer": self.layer,
            "summary": self.summary,
            "raw_entries": [item.to_state() for item in self.raw_entries],
            "distilled_entries": [item.to_state() for item in self.distilled_entries],
            "raw_count": len(self.raw_entries),
            "distilled_count": len(self.distilled_entries),
        }

    @classmethod
    def empty(cls, layer: str, *, summary: str) -> MemoryLayer:
        return cls(layer=layer, summary=summary)

    @classmethod
    def from_state(cls, raw: object) -> MemoryLayer | None:
        raw_dict = _dict(raw)
        layer = _string(raw_dict.get("layer"))
        summary = _string(raw_dict.get("summary"))
        if layer is None or summary is None:
            return None
        raw_entries: list[ContextRecord] = []
        for item in _list(raw_dict.get("raw_entries")):
            parsed = ContextRecord.from_state(item)
            if parsed is not None:
                raw_entries.append(parsed)
        distilled_entries: list[ContextRecord] = []
        for item in _list(raw_dict.get("distilled_entries")):
            parsed = ContextRecord.from_state(item)
            if parsed is not None:
                distilled_entries.append(parsed)
        return cls(
            layer=layer,
            raw_entries=raw_entries,
            distilled_entries=distilled_entries,
            summary=summary,
        )


@dataclass(frozen=True)
class MemoryState:
    working: MemoryLayer
    session: MemoryLayer
    project: MemoryLayer
    promotions: list[MemoryOperation] = field(default_factory=list)
    demotions: list[MemoryOperation] = field(default_factory=list)
    last_updated_at: str | None = None

    def to_state(self) -> dict[str, object]:
        return {
            "working": self.working.to_state(),
            "session": self.session.to_state(),
            "project": self.project.to_state(),
            "promotions": [item.to_state() for item in self.promotions],
            "demotions": [item.to_state() for item in self.demotions],
            "last_updated_at": self.last_updated_at,
            "summary": self.summary,
        }

    @property
    def summary(self) -> str:
        return " | ".join(
            [
                f"working={len(self.working.raw_entries)}/{len(self.working.distilled_entries)}",
                self.session.summary,
                self.project.summary,
            ]
        )

    @classmethod
    def empty(cls) -> MemoryState:
        return cls(
            working=MemoryLayer.empty("working", summary="Working memory is empty."),
            session=MemoryLayer.empty("session", summary="Session memory is empty."),
            project=MemoryLayer.empty("project", summary="Project memory is scaffolded."),
            promotions=[],
            demotions=[],
            last_updated_at=None,
        )

    @classmethod
    def from_state(cls, raw: object) -> MemoryState:
        raw_dict = _dict(raw)
        working = MemoryLayer.from_state(raw_dict.get("working"))
        session = MemoryLayer.from_state(raw_dict.get("session"))
        project = MemoryLayer.from_state(raw_dict.get("project"))
        if working is None or session is None or project is None:
            return cls.empty()
        promotions: list[MemoryOperation] = []
        for item in _list(raw_dict.get("promotions")):
            parsed = MemoryOperation.from_state(item)
            if parsed is not None:
                promotions.append(parsed)
        demotions: list[MemoryOperation] = []
        for item in _list(raw_dict.get("demotions")):
            parsed = MemoryOperation.from_state(item)
            if parsed is not None:
                demotions.append(parsed)
        return cls(
            working=working,
            session=session,
            project=project,
            promotions=promotions,
            demotions=demotions,
            last_updated_at=_string(raw_dict.get("last_updated_at")),
        )


@dataclass(frozen=True)
class ProjectionLevel:
    level: int
    label: str
    summary: str
    entries: list[ContextRecord] = field(default_factory=list)

    @property
    def citation_count(self) -> int:
        return sum(item.citation_count for item in self.entries)

    def to_state(self) -> dict[str, object]:
        return {
            "level": self.level,
            "label": self.label,
            "summary": self.summary,
            "citation_count": self.citation_count,
            "entries": [entry.to_state() for entry in self.entries],
        }

    @classmethod
    def from_state(cls, raw: object) -> ProjectionLevel | None:
        raw_dict = _dict(raw)
        level_raw = raw_dict.get("level")
        level: int | None = level_raw if isinstance(level_raw, int) else None
        label = _string(raw_dict.get("label"))
        summary = _string(raw_dict.get("summary"))
        if level is None or label is None or summary is None:
            return None
        entries: list[ContextRecord] = []
        for item in _list(raw_dict.get("entries")):
            parsed = ContextRecord.from_state(item)
            if parsed is not None:
                entries.append(parsed)
        return cls(level=level, label=label, summary=summary, entries=entries)


@dataclass(frozen=True)
class ContextProjection:
    projection_id: str
    active_level: int
    summary: str
    levels: list[ProjectionLevel] = field(default_factory=list)

    def to_state(self) -> dict[str, object]:
        return {
            "projection_id": self.projection_id,
            "active_level": self.active_level,
            "summary": self.summary,
            "levels": [level.to_state() for level in self.levels],
        }

    @classmethod
    def empty(cls) -> ContextProjection:
        return cls(
            projection_id="projection:empty",
            active_level=1,
            summary="Context projection has not been built yet.",
            levels=[],
        )

    @classmethod
    def from_state(cls, raw: object) -> ContextProjection:
        raw_dict = _dict(raw)
        projection_id = _string(raw_dict.get("projection_id"))
        active_level_raw = raw_dict.get("active_level")
        active_level: int | None = active_level_raw if isinstance(active_level_raw, int) else None
        summary = _string(raw_dict.get("summary"))
        if projection_id is None or active_level is None or summary is None:
            return cls.empty()
        levels: list[ProjectionLevel] = []
        for item in _list(raw_dict.get("levels")):
            parsed = ProjectionLevel.from_state(item)
            if parsed is not None:
                levels.append(parsed)
        return cls(
            projection_id=projection_id,
            active_level=active_level,
            summary=summary,
            levels=levels,
        )


@dataclass(frozen=True)
class ContextSnapshot:
    retrieval: RetrievalState
    memory: MemoryState
    projection: ContextProjection
    prompting: dict[str, object] = field(default_factory=dict)
    compact_runtime: dict[str, object] = field(default_factory=dict)

    def to_state(self) -> dict[str, object]:
        return {
            "retrieval": self.retrieval.to_state(),
            "memory": self.memory.to_state(),
            "projection": self.projection.to_state(),
            "prompting": dict(self.prompting),
            "compact_runtime": dict(self.compact_runtime),
        }

    @classmethod
    def empty(cls) -> ContextSnapshot:
        return cls(
            retrieval=RetrievalState.empty(),
            memory=MemoryState.empty(),
            projection=ContextProjection.empty(),
            prompting={},
            compact_runtime={},
        )

    @classmethod
    def from_state(cls, raw: object) -> ContextSnapshot:
        raw_dict = _dict(raw)
        return cls(
            retrieval=RetrievalState.from_state(raw_dict.get("retrieval")),
            memory=MemoryState.from_state(raw_dict.get("memory")),
            projection=ContextProjection.from_state(raw_dict.get("projection")),
            prompting=_dict(raw_dict.get("prompting")),
            compact_runtime=_dict(raw_dict.get("compact_runtime")),
        )
