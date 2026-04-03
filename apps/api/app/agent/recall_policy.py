from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecallPolicy:
    top_k: int = 3
    freshness_bias: float = 1.0
    task_match_bias: float = 2.0
    recent_tool_bias: float = 1.25
    already_surfaced_penalty: float = 4.0
    compact_boundary_bias: float = 0.5

    def to_state(self) -> dict[str, object]:
        return {
            "top_k": self.top_k,
            "freshness_bias": self.freshness_bias,
            "task_match_bias": self.task_match_bias,
            "recent_tool_bias": self.recent_tool_bias,
            "already_surfaced_penalty": self.already_surfaced_penalty,
            "compact_boundary_bias": self.compact_boundary_bias,
        }

    @classmethod
    def from_state(cls, raw: object) -> RecallPolicy:
        if not isinstance(raw, dict):
            return cls()
        return cls(
            top_k=int(raw.get("top_k", 3)) if isinstance(raw.get("top_k"), int) else 3,
            freshness_bias=float(raw.get("freshness_bias", 1.0))
            if isinstance(raw.get("freshness_bias"), int | float)
            else 1.0,
            task_match_bias=float(raw.get("task_match_bias", 2.0))
            if isinstance(raw.get("task_match_bias"), int | float)
            else 2.0,
            recent_tool_bias=float(raw.get("recent_tool_bias", 1.25))
            if isinstance(raw.get("recent_tool_bias"), int | float)
            else 1.25,
            already_surfaced_penalty=float(raw.get("already_surfaced_penalty", 4.0))
            if isinstance(raw.get("already_surfaced_penalty"), int | float)
            else 4.0,
            compact_boundary_bias=float(raw.get("compact_boundary_bias", 0.5))
            if isinstance(raw.get("compact_boundary_bias"), int | float)
            else 0.5,
        )
