from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .messages import GenerationCallbacks
from .query_engine import BaseQueryEngine


class HarnessRuntime:
    def __init__(self, *, query_engine: BaseQueryEngine) -> None:
        self._query_engine = query_engine

    @property
    def query_engine(self) -> BaseQueryEngine:
        return self._query_engine

    @property
    def session_state(self) -> Any | None:
        return self._query_engine.session_state

    async def generate_reply(
        self,
        *,
        execute_tool: Callable[[object], Awaitable[object]] | None,
        callbacks: GenerationCallbacks | None,
    ) -> str:
        return await self._query_engine.submit_message(
            execute_tool=execute_tool,
            callbacks=callbacks,
        )
