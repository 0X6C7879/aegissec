from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
from typing import Any

from app.observability.span import AgentPerformanceSpan

DEFAULT_MAX_SPANS = 1000


class PerformanceRecorder:
    def __init__(self, max_spans: int = DEFAULT_MAX_SPANS) -> None:
        self._spans: deque[AgentPerformanceSpan] = deque(maxlen=max_spans)
        self._lock = Lock()

    def start_span(self, name: str, session_id: str | None = None) -> AgentPerformanceSpan:
        return AgentPerformanceSpan(name=name, session_id=session_id)

    def record(self, span: AgentPerformanceSpan) -> None:
        with self._lock:
            self._spans.append(span)

    def get_recent(self, n: int = 50) -> list[dict[str, Any]]:
        if n <= 0:
            return []
        with self._lock:
            recent_spans = list(self._spans)[-n:]
        return [span.to_dict() for span in recent_spans]

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            spans = list(self._spans)

        durations_by_name: dict[str, list[float]] = defaultdict(list)
        for span in spans:
            if span.duration_ms is not None:
                durations_by_name[span.name].append(span.duration_ms)

        stats_by_name: dict[str, dict[str, float | int | None]] = {}
        for name, durations in durations_by_name.items():
            sorted_durations = sorted(durations)
            stats_by_name[name] = {
                "count": len(sorted_durations),
                "avg_duration_ms": sum(sorted_durations) / len(sorted_durations),
                "p50_ms": _percentile(sorted_durations, 50),
                "p95_ms": _percentile(sorted_durations, 95),
                "p99_ms": _percentile(sorted_durations, 99),
            }

        return {
            "total_spans": len(spans),
            "by_name": stats_by_name,
        }


_instance: PerformanceRecorder | None = None
_instance_lock = Lock()


def get_recorder() -> PerformanceRecorder:
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = PerformanceRecorder()
    return _instance


def _percentile(values: list[float], percentile: int) -> float | None:
    if not values:
        return None
    index = round((percentile / 100) * (len(values) - 1))
    return values[index]
