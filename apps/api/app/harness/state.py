from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class HarnessRetrievalManifest:
    query_text: str = ""
    memory_key: str = ""
    recalled_entry_ids: list[str] = field(default_factory=list)
    source_labels: list[str] = field(default_factory=list)
    rendered_retrieval_fragment: str = ""
    rendered_memory_fragment: str = ""


@dataclass(slots=True)
class HarnessCompactionState:
    recent_turns: int = 0
    last_compacted_turn: int = 0
    active_compact_fragment: str = ""
    durable_artifact_ref: str | None = None
    mode: str = "none"
    archived_message_count: int = 0


@dataclass(slots=True)
class HarnessSemanticDelta:
    semantic_id: str
    source: str
    evidence_ids: list[str] = field(default_factory=list)
    hypothesis_ids: list[str] = field(default_factory=list)
    graph_hints: list[dict[str, object]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    recent_entities: list[str] = field(default_factory=list)
    recent_tools: list[str] = field(default_factory=list)
    reason: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class HarnessSemanticState:
    active_hypotheses: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    graph_hints: list[dict[str, object]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    recent_entities: list[str] = field(default_factory=list)
    recent_tools: list[str] = field(default_factory=list)
    reason: str | None = None
    pending_deltas: list[HarnessSemanticDelta] = field(default_factory=list)


@dataclass(slots=True)
class HarnessSessionState:
    session_id: str
    memory_key: str
    current_phase: str | None = None
    goal: str | None = None
    scenario_type: str | None = None
    retrieval_manifest: HarnessRetrievalManifest = field(default_factory=HarnessRetrievalManifest)
    compaction: HarnessCompactionState = field(default_factory=HarnessCompactionState)
    semantic: HarnessSemanticState = field(default_factory=HarnessSemanticState)
    swarm: dict[str, object] = field(default_factory=dict)
