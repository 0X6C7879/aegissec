from __future__ import annotations

import importlib
import json
from typing import Any


def semantic_snapshot_from_state(session_state: Any | None) -> dict[str, object]:
    if session_state is None:
        return {}
    semantic_state = getattr(session_state, "semantic", None)
    if semantic_state is None:
        return {}
    return {
        "active_hypotheses": list(dict.fromkeys(getattr(semantic_state, "active_hypotheses", []))),
        "evidence_ids": list(dict.fromkeys(getattr(semantic_state, "evidence_ids", []))),
        "graph_hints": [dict(item) for item in getattr(semantic_state, "graph_hints", [])],
        "artifacts": list(dict.fromkeys(getattr(semantic_state, "artifacts", []))),
        "recent_entities": list(dict.fromkeys(getattr(semantic_state, "recent_entities", []))),
        "recent_tools": list(dict.fromkeys(getattr(semantic_state, "recent_tools", []))),
        "reason": getattr(semantic_state, "reason", None),
    }


def clear_pending_semantic_deltas(session_state: Any | None) -> None:
    semantic_state = getattr(session_state, "semantic", None)
    if semantic_state is not None:
        semantic_state.pending_deltas.clear()


def stage_semantic_deltas(session_state: Any | None, raw_deltas: list[dict[str, Any]]) -> None:
    if session_state is None or not raw_deltas:
        return
    HarnessSemanticDelta = importlib.import_module("app.harness.state").HarnessSemanticDelta
    semantic_state = getattr(session_state, "semantic", None)
    if semantic_state is None:
        return

    def merge_unique(target: list[str], values: list[str]) -> None:
        seen = set(target)
        for value in values:
            if isinstance(value, str) and value and value not in seen:
                target.append(value)
                seen.add(value)

    for raw_delta in raw_deltas:
        if not isinstance(raw_delta, dict):
            continue
        delta = HarnessSemanticDelta(
            semantic_id=str(raw_delta.get("semantic_id") or ""),
            source=str(raw_delta.get("source") or "tool"),
            evidence_ids=[
                str(item) for item in raw_delta.get("evidence_ids", []) if isinstance(item, str)
            ],
            hypothesis_ids=[
                str(item) for item in raw_delta.get("hypothesis_ids", []) if isinstance(item, str)
            ],
            graph_hints=[
                dict(item) for item in raw_delta.get("graph_hints", []) if isinstance(item, dict)
            ],
            artifacts=[
                str(item) for item in raw_delta.get("artifacts", []) if isinstance(item, str)
            ],
            recent_entities=[
                str(item) for item in raw_delta.get("recent_entities", []) if isinstance(item, str)
            ],
            recent_tools=[
                str(item) for item in raw_delta.get("recent_tools", []) if isinstance(item, str)
            ],
            reason=(str(raw_delta.get("reason")) if raw_delta.get("reason") is not None else None),
            metadata=(
                dict(raw_delta.get("metadata", {}))
                if isinstance(raw_delta.get("metadata"), dict)
                else {}
            ),
        )
        semantic_state.pending_deltas.append(delta)
        merge_unique(semantic_state.evidence_ids, delta.evidence_ids)
        merge_unique(semantic_state.active_hypotheses, delta.hypothesis_ids)
        merge_unique(semantic_state.artifacts, delta.artifacts)
        merge_unique(semantic_state.recent_entities, delta.recent_entities)
        merge_unique(semantic_state.recent_tools, delta.recent_tools)
        if delta.reason:
            semantic_state.reason = delta.reason
        existing_keys = {
            json.dumps(item, ensure_ascii=False, sort_keys=True)
            for item in semantic_state.graph_hints
            if isinstance(item, dict)
        }
        for hint in delta.graph_hints:
            hint_key = json.dumps(hint, ensure_ascii=False, sort_keys=True)
            if hint_key not in existing_keys:
                semantic_state.graph_hints.append(hint)
                existing_keys.add(hint_key)


def stage_swarm_notification_semantics(
    session_state: Any | None,
    notifications: list[dict[str, Any]],
) -> None:
    semantic_deltas: list[dict[str, Any]] = []
    for notification in notifications:
        if not isinstance(notification, dict):
            continue
        semantic_deltas.append(
            {
                "semantic_id": str(
                    notification.get("task_id")
                    or notification.get("agent_id")
                    or notification.get("summary")
                    or "swarm"
                ),
                "source": "swarm_notification",
                "evidence_ids": notification.get("evidence_ids", []),
                "hypothesis_ids": notification.get("hypothesis_ids", []),
                "graph_hints": notification.get("graph_updates", []),
                "artifacts": notification.get("artifacts", []),
                "reason": notification.get("reason") or notification.get("summary"),
                "metadata": {
                    "agent_id": notification.get("agent_id"),
                    "task_id": notification.get("task_id"),
                    "status": notification.get("status"),
                },
            }
        )
    stage_semantic_deltas(session_state, semantic_deltas)
