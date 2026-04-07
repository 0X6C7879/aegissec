from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SwarmNotificationStatus(StrEnum):
    PLANNED = "planned"
    STARTED = "started"
    MESSAGE = "message"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class SwarmNotification:
    agent_id: str
    status: SwarmNotificationStatus
    summary: str
    task_id: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    hypothesis_ids: list[str] = field(default_factory=list)
    graph_updates: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "status": self.status.value,
            "summary": self.summary,
            "task_id": self.task_id,
            "result": dict(self.result),
            "usage": dict(self.usage),
            "evidence_ids": list(self.evidence_ids),
            "hypothesis_ids": list(self.hypothesis_ids),
            "graph_updates": [dict(item) for item in self.graph_updates],
            "artifacts": list(self.artifacts),
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }
