from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Literal


@dataclass
class AgentPerformanceSpan:
    name: str
    session_id: str | None = None
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    duration_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def __enter__(self) -> AgentPerformanceSpan:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        error = f"{type(exc).__name__}: {exc}" if exc is not None else None
        self.finish(error=error)

        from app.observability.recorder import get_recorder

        get_recorder().record(self)
        return False

    def add_metadata(self, key: str, value: Any) -> None:
        self.metadata[key] = value

    def finish(self, error: str | None = None) -> None:
        if self.end_time is None:
            self.end_time = time.monotonic()
            self.duration_ms = (self.end_time - self.start_time) * 1000
        if error is not None:
            self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "session_id": self.session_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "metadata": dict(self.metadata),
            "error": self.error,
        }
