from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field

from app.db.models import AttachmentMetadata


class GenerationCancelledError(Exception):
    pass


@dataclass(slots=True)
class QueuedPrompt:
    content: str
    attachments: list[AttachmentMetadata]
    user_message_id: str
    assistant_message_id: str
    response_future: asyncio.Future[str]


@dataclass(slots=True)
class SessionGenerationState:
    queue: deque[QueuedPrompt] = field(default_factory=deque)
    worker_task: asyncio.Task[None] | None = None
    current_generation_id: str | None = None
    current_assistant_message_id: str | None = None
    cancel_requested: bool = False
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)


class SessionGenerationManager:
    def __init__(self) -> None:
        self._states: dict[str, SessionGenerationState] = {}
        self._lock = asyncio.Lock()

    async def ensure_session(self, session_id: str) -> bool:
        async with self._lock:
            if session_id in self._states:
                return False
            self._states[session_id] = SessionGenerationState()
            return True

    async def attach_worker(self, session_id: str, worker_task: asyncio.Task[None]) -> None:
        async with self._lock:
            state = self._states.get(session_id)
            if state is None:
                state = SessionGenerationState()
                self._states[session_id] = state
            state.worker_task = worker_task

    async def enqueue_prompt(self, session_id: str, prompt: QueuedPrompt) -> int:
        async with self._lock:
            state = self._states.setdefault(session_id, SessionGenerationState())
            state.queue.append(prompt)
            return len(state.queue)

    async def pop_next_prompt(self, session_id: str) -> QueuedPrompt | None:
        async with self._lock:
            state = self._states.get(session_id)
            if state is None or not state.queue:
                return None
            return state.queue.popleft()

    async def begin_generation(
        self,
        session_id: str,
        *,
        generation_id: str,
        assistant_message_id: str,
    ) -> int:
        async with self._lock:
            state = self._states.setdefault(session_id, SessionGenerationState())
            state.current_generation_id = generation_id
            state.current_assistant_message_id = assistant_message_id
            state.cancel_requested = False
            state.cancel_event.clear()
            return len(state.queue)

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

    async def cancel_session(self, session_id: str) -> tuple[str | None, str | None, int]:
        async with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return None, None, 0

            state.cancel_requested = True
            state.cancel_event.set()
            worker_task = state.worker_task
            queue_size = len(state.queue)
            generation_id = state.current_generation_id
            message_id = state.current_assistant_message_id

        if worker_task is not None:
            worker_task.cancel()

        return generation_id, message_id, queue_size

    async def fail_pending(self, session_id: str, error: Exception) -> None:
        async with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return
            pending_prompts = list(state.queue)
            state.queue.clear()

        for prompt in pending_prompts:
            if not prompt.response_future.done():
                prompt.response_future.set_exception(error)

    async def get_cancel_event(self, session_id: str) -> asyncio.Event:
        async with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return asyncio.Event()
            return state.cancel_event

    async def finish_if_idle(self, session_id: str, worker_task: asyncio.Task[None]) -> bool:
        async with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return True
            if state.worker_task is not worker_task:
                return False
            if state.queue or state.current_generation_id is not None:
                return False
            self._states.pop(session_id, None)
            return True


generation_manager = SessionGenerationManager()


def get_generation_manager() -> SessionGenerationManager:
    return generation_manager
