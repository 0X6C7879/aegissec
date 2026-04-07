from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

_current_agent_context: ContextVar[InProcessAgentContext | None] = ContextVar(
    "harness_swarm_agent_context",
    default=None,
)


@dataclass(slots=True)
class InProcessAgentContext:
    agent_id: str
    profile_name: str
    session_id: str
    objective: str
    task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass(slots=True)
class InProcessAgentHandle:
    context: InProcessAgentContext
    task: asyncio.Task[dict[str, Any] | None]


def get_in_process_agent_context() -> InProcessAgentContext | None:
    return _current_agent_context.get()


class InProcessSwarmBackend:
    def __init__(self) -> None:
        self._handles: dict[str, InProcessAgentHandle] = {}

    def start(
        self,
        *,
        context: InProcessAgentContext,
        runner: Callable[[InProcessAgentContext], Awaitable[dict[str, Any] | None]],
    ) -> InProcessAgentHandle:
        async def _run() -> dict[str, Any] | None:
            token = _current_agent_context.set(context)
            try:
                return await runner(context)
            finally:
                _current_agent_context.reset(token)

        handle = InProcessAgentHandle(context=context, task=asyncio.create_task(_run()))
        self._handles[context.agent_id] = handle
        return handle

    async def stop(self, agent_id: str, *, force: bool = False) -> None:
        handle = self._handles.get(agent_id)
        if handle is None:
            return
        handle.context.cancel_event.set()
        if force:
            handle.task.cancel()
        try:
            await asyncio.wait_for(handle.task, timeout=0.05)
        except Exception:  # noqa: BLE001
            if force:
                handle.task.cancel()
        finally:
            self._handles.pop(agent_id, None)

    def list_running(self) -> list[str]:
        return [agent_id for agent_id, handle in self._handles.items() if not handle.task.done()]
