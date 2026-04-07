from __future__ import annotations

from typing import Any

from app.core.events import SessionEventType


def swarm_notification_to_event_type(status: str) -> SessionEventType:
    normalized_status = status.lower()
    if normalized_status == "planned":
        return SessionEventType.TASK_PLANNED
    if normalized_status == "started":
        return SessionEventType.TASK_STARTED
    if normalized_status in {"completed", "failed", "cancelled"}:
        return SessionEventType.TASK_FINISHED
    return SessionEventType.WORKFLOW_TASK_UPDATED


def semantic_event_payload(semantic_snapshot: dict[str, object] | None) -> dict[str, Any]:
    if not isinstance(semantic_snapshot, dict) or not semantic_snapshot:
        return {}
    return {
        "evidence_ids": semantic_snapshot.get("evidence_ids", []),
        "hypothesis_ids": semantic_snapshot.get("active_hypotheses", []),
        "graph_updates": semantic_snapshot.get("graph_hints", []),
        "artifacts": semantic_snapshot.get("artifacts", []),
        "reason": semantic_snapshot.get("reason"),
    }


def semantic_trace_payload(semantic_snapshot: dict[str, object] | None) -> dict[str, object]:
    if not isinstance(semantic_snapshot, dict) or not semantic_snapshot:
        return {}
    return {"semantic_state": semantic_snapshot}
