from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession

from app.db.repositories import SessionRepository


class GenerationCancelledError(Exception):
    pass


@dataclass(slots=True)
class SessionGenerationState:
    worker_task: asyncio.Task[None] | None = None
    current_generation_id: str | None = None
    current_assistant_message_id: str | None = None
    cancel_requested: bool = False
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    response_futures: dict[str, asyncio.Future[str]] = field(default_factory=dict)
    continuation_futures: dict[str, asyncio.Future[dict[str, object]]] = field(default_factory=dict)


class SessionGenerationManager:
    def __init__(self) -> None:
        self._states: dict[str, SessionGenerationState] = {}
        self._lock = asyncio.Lock()

    async def should_start_worker(self, session_id: str) -> bool:
        async with self._lock:
            state = self._states.setdefault(session_id, SessionGenerationState())
            return state.worker_task is None or state.worker_task.done()

    async def attach_worker(self, session_id: str, worker_task: asyncio.Task[None]) -> None:
        async with self._lock:
            state = self._states.setdefault(session_id, SessionGenerationState())
            state.worker_task = worker_task

    async def register_future(self, session_id: str, generation_id: str) -> asyncio.Future[str]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        async with self._lock:
            state = self._states.setdefault(session_id, SessionGenerationState())
            state.response_futures[generation_id] = future
        return future

    async def resolve_future(self, session_id: str, generation_id: str, value: str) -> None:
        async with self._lock:
            state = self._states.get(session_id)
            future = None if state is None else state.response_futures.pop(generation_id, None)
        if future is not None and not future.done():
            future.set_result(value)

    async def reject_future(self, session_id: str, generation_id: str, error: Exception) -> None:
        async with self._lock:
            state = self._states.get(session_id)
            future = None if state is None else state.response_futures.pop(generation_id, None)
        if future is not None and not future.done():
            future.set_exception(error)

    async def reject_pending(
        self,
        session_id: str,
        error: Exception,
        *,
        exclude_generation_ids: set[str] | None = None,
    ) -> None:
        async with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return
            excluded = exclude_generation_ids or set()
            futures = [
                (generation_id, future)
                for generation_id, future in state.response_futures.items()
                if generation_id not in excluded
            ]
            state.response_futures = {
                generation_id: future
                for generation_id, future in state.response_futures.items()
                if generation_id in excluded
            }
        for _, future in futures:
            if not future.done():
                future.set_exception(error)

    async def register_continuation_future(
        self,
        session_id: str,
        continuation_token: str,
    ) -> asyncio.Future[dict[str, object]]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, object]] = loop.create_future()
        async with self._lock:
            state = self._states.setdefault(session_id, SessionGenerationState())
            existing = state.continuation_futures.pop(continuation_token, None)
            if existing is not None and not existing.done():
                existing.set_exception(GenerationCancelledError("Continuation was replaced."))
            state.continuation_futures[continuation_token] = future
        return future

    async def has_continuation_future(self, session_id: str, continuation_token: str) -> bool:
        async with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return False
            future = state.continuation_futures.get(continuation_token)
            return future is not None and not future.done()

    async def resolve_continuation_future(
        self,
        session_id: str,
        continuation_token: str,
        payload: dict[str, object],
    ) -> None:
        async with self._lock:
            state = self._states.get(session_id)
            future = (
                None if state is None else state.continuation_futures.pop(continuation_token, None)
            )
        if future is not None and not future.done():
            future.set_result(dict(payload))

    async def reject_continuation_futures(
        self,
        session_id: str,
        error: Exception,
    ) -> None:
        async with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return
            futures = list(state.continuation_futures.values())
            state.continuation_futures = {}
        for future in futures:
            if not future.done():
                future.set_exception(error)

    async def begin_generation(
        self,
        session_id: str,
        *,
        generation_id: str,
        assistant_message_id: str,
    ) -> None:
        async with self._lock:
            state = self._states.setdefault(session_id, SessionGenerationState())
            state.current_generation_id = generation_id
            state.current_assistant_message_id = assistant_message_id
            state.cancel_requested = False
            state.cancel_event.clear()

    async def clear_current_generation(self, session_id: str, generation_id: str) -> None:
        async with self._lock:
            state = self._states.get(session_id)
            if state is None or state.current_generation_id != generation_id:
                return
            state.current_generation_id = None
            state.current_assistant_message_id = None
            state.cancel_requested = False
            state.cancel_event.clear()

    async def is_cancel_requested(self, session_id: str, generation_id: str) -> bool:
        async with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return False
            return state.current_generation_id == generation_id and state.cancel_requested

    async def cancel_generation(
        self,
        session_id: str,
        generation_id: str | None,
    ) -> tuple[str | None, str | None]:
        async with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return None, None

            current_generation_id = state.current_generation_id
            if generation_id is not None and current_generation_id != generation_id:
                return None, None

            state.cancel_requested = True
            state.cancel_event.set()
            worker_task = state.worker_task
            assistant_message_id = state.current_assistant_message_id

        if worker_task is not None:
            worker_task.cancel()

        await self.reject_continuation_futures(
            session_id,
            GenerationCancelledError("Active generation was cancelled."),
        )

        return current_generation_id, assistant_message_id

    async def get_cancel_event(self, session_id: str) -> asyncio.Event:
        async with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return asyncio.Event()
            return state.cancel_event

    async def worker_finished(self, session_id: str, worker_task: asyncio.Task[None]) -> None:
        async with self._lock:
            state = self._states.get(session_id)
            if state is None or state.worker_task is not worker_task:
                return
            state.worker_task = None


class GenerationPausedError(Exception):
    def __init__(self, detail: str, *, continuation_token: str, action: str) -> None:
        super().__init__(detail)
        self.continuation_token = continuation_token
        self.action = action


generation_manager = SessionGenerationManager()


def get_generation_manager() -> SessionGenerationManager:
    return generation_manager


def recover_abandoned_generations(db_engine: Engine) -> int:
    with DBSession(db_engine) as db_session:
        repository = SessionRepository(db_session)
        return repository.recover_abandoned_generations()
