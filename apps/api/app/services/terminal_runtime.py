from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from threading import Thread
from typing import Literal, Protocol, cast

import docker
from docker.errors import DockerException, NotFound
from docker.models.containers import Container
from fastapi import FastAPI
from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession

from app.core.events import SessionEvent, SessionEventBroker, SessionEventType
from app.core.settings import Settings
from app.db.models import ExecutionStatus, RuntimePolicy, RuntimeTerminalJobStatus, utc_now
from app.db.repositories import (
    RunLogRepository,
    RuntimeRepository,
    SessionRepository,
    TerminalRepository,
)
from app.services.runtime import (
    DockerRuntimeBackend,
    RuntimeOperationError,
    RuntimeService,
    get_runtime_backend,
)
from app.services.terminal_sessions import (
    SessionShellService,
    TerminalRuntimeSnapshot,
    terminal_audit_payload,
    terminal_job_audit_payload,
)


class TerminalRuntimeError(Exception):
    pass


class TerminalNotFoundError(TerminalRuntimeError):
    pass


class TerminalClosedError(TerminalRuntimeError):
    pass


class TerminalAlreadyAttachedError(TerminalRuntimeError):
    pass


class TerminalBackendUnavailableError(TerminalRuntimeError):
    pass


class TerminalNotAttachedError(TerminalRuntimeError):
    pass


class TerminalJobAlreadyRunningError(TerminalRuntimeError):
    pass


@dataclass(slots=True)
class OrphanTerminalRecoveryResult:
    cancelled_jobs: int = 0
    closed_terminals: int = 0
    runtime_stop_attempted: bool = False


TERMINAL_CLIENT_FRAME_MAX_BYTES = 16 * 1024
TERMINAL_CLIENT_QUEUE_MAXSIZE = 256
TERMINAL_BACKEND_EVENT_QUEUE_MAXSIZE = 256
TERMINAL_ALLOWED_SIGNALS = frozenset({"HUP", "INT", "KILL", "QUIT", "TERM"})
TERMINAL_CLOSE_WAIT_SECONDS = 1.0
TERMINAL_JOB_OUTPUT_MAX_CHARS = 8_192
TERMINAL_BUFFER_MAX_CHARS = 50_000


def normalize_terminal_signal_name(signal_name: str) -> str:
    normalized = signal_name.strip().upper()
    if normalized == "INTERRUPT":
        normalized = "INT"
    if normalized not in TERMINAL_ALLOWED_SIGNALS:
        raise TerminalRuntimeError("unsupported terminal signal")
    return normalized


@dataclass(slots=True)
class TerminalBackendEvent:
    kind: str
    data: bytes | None = None
    exit_code: int | None = None
    reason: str | None = None
    message: str | None = None

    @classmethod
    def output(cls, *, data: bytes) -> TerminalBackendEvent:
        return cls(kind="output", data=data)

    @classmethod
    def exit(cls, *, exit_code: int | None, reason: str) -> TerminalBackendEvent:
        return cls(kind="exit", exit_code=exit_code, reason=reason)

    @classmethod
    def error(cls, *, message: str) -> TerminalBackendEvent:
        return cls(kind="error", message=message)


class TerminalProcess(Protocol):
    events: asyncio.Queue[TerminalBackendEvent]

    async def send_input(self, data: bytes) -> None: ...

    async def resize(self, cols: int, rows: int) -> None: ...

    async def send_signal(self, signal_name: str) -> None: ...

    async def send_eof(self) -> None: ...

    async def close(self, *, reason: str) -> None: ...


class TerminalBackend(Protocol):
    async def open_terminal(
        self,
        *,
        terminal_id: str,
        shell: str,
        cwd: str,
        cols: int,
        rows: int,
    ) -> TerminalProcess: ...

    async def shutdown(self) -> None: ...


@dataclass(slots=True)
class LiveTerminalHandle:
    session_id: str
    terminal_id: str
    job_id: str
    process: TerminalProcess | None = None
    queue: asyncio.Queue[dict[str, object]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=TERMINAL_CLIENT_QUEUE_MAXSIZE)
    )
    attached: bool = False
    finalized: bool = False
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    finalize_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    detach_task: asyncio.Task[None] | None = None
    detach_timer: asyncio.TimerHandle | None = None
    pump_task: asyncio.Task[None] | None = None
    detach_generation: int = 0
    output_buffer: str = ""
    reattach_deadline: datetime | None = None


@dataclass(slots=True)
class LiveTerminalJobHandle:
    session_id: str
    terminal_id: str
    job_id: str
    command: str
    timeout_seconds: int
    artifact_paths: list[str]
    started_at: datetime
    process: TerminalProcess | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    finalized: bool = False
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    finalize_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    timeout_task: asyncio.Task[None] | None = None
    timeout_handle: asyncio.TimerHandle | None = None
    pump_task: asyncio.Task[None] | None = None


class LiveTerminalRegistry:
    def __init__(self) -> None:
        self._handles: dict[str, LiveTerminalHandle] = {}
        self._lock = asyncio.Lock()

    async def attach(self, *, terminal_id: str, handle: LiveTerminalHandle) -> LiveTerminalHandle:
        async with self._lock:
            existing = self._handles.get(terminal_id)
            if existing is not None and existing.attached:
                raise TerminalAlreadyAttachedError("terminal already attached")
            handle.attached = True
            handle.detach_generation += 1
            handle.reattach_deadline = None
            self._handles[terminal_id] = handle
            return handle

    async def get(self, *, terminal_id: str) -> LiveTerminalHandle | None:
        async with self._lock:
            return self._handles.get(terminal_id)

    async def acquire_or_create(
        self,
        *,
        session_id: str,
        terminal_id: str,
        create_handle: Callable[[], Awaitable[LiveTerminalHandle]],
    ) -> tuple[LiveTerminalHandle, bool]:
        async with self._lock:
            existing = self._handles.get(terminal_id)
            if existing is not None:
                if existing.session_id != session_id:
                    raise TerminalNotFoundError("Terminal not found")
                if existing.attached:
                    raise TerminalAlreadyAttachedError("terminal already attached")
                existing.attached = True
                existing.detach_generation += 1
                existing.reattach_deadline = None
                if existing.detach_task is not None:
                    existing.detach_task.cancel()
                    existing.detach_task = None
                if existing.detach_timer is not None:
                    existing.detach_timer.cancel()
                    existing.detach_timer = None
                return existing, False

        handle = await create_handle()
        async with self._lock:
            existing = self._handles.get(terminal_id)
            if existing is not None:
                if existing.session_id != session_id:
                    raise TerminalNotFoundError("Terminal not found")
                if existing.attached:
                    raise TerminalAlreadyAttachedError("terminal already attached")
                existing.attached = True
                existing.detach_generation += 1
                existing.reattach_deadline = None
                if existing.detach_task is not None:
                    existing.detach_task.cancel()
                    existing.detach_task = None
                if existing.detach_timer is not None:
                    existing.detach_timer.cancel()
                    existing.detach_timer = None
                return existing, False

            handle.attached = True
            handle.detach_generation += 1
            handle.reattach_deadline = None
            self._handles[terminal_id] = handle
            return handle, True

    async def remove(self, *, terminal_id: str) -> None:
        async with self._lock:
            self._handles.pop(terminal_id, None)

    async def list_handles(self) -> list[LiveTerminalHandle]:
        async with self._lock:
            return list(self._handles.values())


class LiveTerminalJobRegistry:
    def __init__(self) -> None:
        self._handles: dict[str, LiveTerminalJobHandle] = {}
        self._lock = asyncio.Lock()
        self._terminal_start_locks: dict[str, asyncio.Lock] = {}

    async def put(self, handle: LiveTerminalJobHandle) -> None:
        async with self._lock:
            self._handles[handle.job_id] = handle

    async def get(self, *, job_id: str) -> LiveTerminalJobHandle | None:
        async with self._lock:
            return self._handles.get(job_id)

    async def remove(self, *, job_id: str) -> None:
        async with self._lock:
            self._handles.pop(job_id, None)

    async def active_job_ids_for_session(self, *, session_id: str) -> set[str]:
        async with self._lock:
            return {
                job_id
                for job_id, handle in self._handles.items()
                if handle.session_id == session_id and not handle.finalized
            }

    async def list_handles(self) -> list[LiveTerminalJobHandle]:
        async with self._lock:
            return list(self._handles.values())

    @asynccontextmanager
    async def terminal_start_lock(self, terminal_id: str) -> AsyncIterator[None]:
        async with self._lock:
            lock = self._terminal_start_locks.setdefault(terminal_id, asyncio.Lock())
        async with lock:
            yield


class DockerExecTerminalProcess:
    def __init__(
        self,
        *,
        api_client: docker.api.client.APIClient,
        container: Container,
        exec_id: str,
        sock: socket.socket,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._api_client = api_client
        self._container = container
        self._exec_id = exec_id
        self._sock = sock
        self._loop = loop
        self._closed = False
        self._overflowed = False
        self.events: asyncio.Queue[TerminalBackendEvent] = asyncio.Queue(
            maxsize=TERMINAL_BACKEND_EVENT_QUEUE_MAXSIZE
        )
        self._reader = Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _push_event(self, event: TerminalBackendEvent) -> None:
        self._loop.call_soon_threadsafe(self._push_event_nowait, event)

    def _push_event_nowait(self, event: TerminalBackendEvent) -> None:
        try:
            self.events.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass

        if event.kind == "output" and not self._overflowed:
            self._overflowed = True
            try:
                self.events.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self.events.put_nowait(
                    TerminalBackendEvent.error(message="terminal output buffer overflow")
                )
            except asyncio.QueueFull:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            return

        try:
            self.events.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            self.events.put_nowait(event)
        except asyncio.QueueFull:
            pass

    async def _get_exec_pid(self) -> int | None:
        exec_details = cast(
            dict[str, object], await asyncio.to_thread(self._api_client.exec_inspect, self._exec_id)
        )
        pid = exec_details.get("Pid")
        if not isinstance(pid, int) or pid <= 0:
            return None
        return pid

    async def _send_kill(self, signal_name: str) -> None:
        pid = await self._get_exec_pid()
        if pid is None:
            return
        normalized = normalize_terminal_signal_name(signal_name)
        await asyncio.to_thread(
            self._container.exec_run,
            ["kill", f"-{normalized}", str(pid)],
            stdout=False,
            stderr=False,
        )

    def _read_loop(self) -> None:
        try:
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    break
                self._push_event(TerminalBackendEvent.output(data=chunk))
        except OSError:
            self._push_event(TerminalBackendEvent.error(message="terminal socket read failed"))
        finally:
            self._emit_exit("exit")

    def _emit_exit(self, reason: str) -> None:
        try:
            exec_details = cast(dict[str, object], self._api_client.exec_inspect(self._exec_id))
            exit_code = exec_details.get("ExitCode")
            normalized_exit_code = exit_code if isinstance(exit_code, int) else None
        except DockerException:
            normalized_exit_code = None
        self._push_event(TerminalBackendEvent.exit(exit_code=normalized_exit_code, reason=reason))

    async def send_input(self, data: bytes) -> None:
        await asyncio.to_thread(self._sock.sendall, data)

    async def resize(self, cols: int, rows: int) -> None:
        await asyncio.to_thread(
            self._api_client.exec_resize, self._exec_id, height=rows, width=cols
        )

    async def send_signal(self, signal_name: str) -> None:
        await self._send_kill(signal_name)

    async def send_eof(self) -> None:
        await self.close(reason="eof")

    async def close(self, *, reason: str) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._send_kill("TERM")
        except (DockerException, TerminalRuntimeError):
            pass
        try:
            self._sock.close()
        finally:
            self._push_event(TerminalBackendEvent.exit(exit_code=None, reason=reason))


class DockerTerminalBackend:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        try:
            client = docker.from_env()
            self._client = client
            self._api_client = client.api
            self._runtime_backend = DockerRuntimeBackend(settings)
        except DockerException as exc:
            raise TerminalBackendUnavailableError(
                "Docker is not available. Start Docker Desktop or the daemon."
            ) from exc
        except RuntimeOperationError as exc:
            raise TerminalBackendUnavailableError(str(exc)) from exc

    async def open_terminal(
        self,
        *,
        terminal_id: str,
        shell: str,
        cwd: str,
        cols: int,
        rows: int,
    ) -> TerminalProcess:
        del terminal_id
        try:
            self._runtime_backend.ensure_started()
            container = self._client.containers.get(self._settings.runtime_container_name)
            exec_payload = self._api_client.exec_create(
                container.id,
                cmd=[shell],
                stdout=True,
                stderr=True,
                stdin=True,
                tty=True,
                workdir=cwd,
            )
            exec_id = exec_payload.get("Id")
            if not isinstance(exec_id, str) or not exec_id:
                raise TerminalBackendUnavailableError("Docker did not return a PTY exec id.")
            raw_socket = self._api_client.exec_start(exec_id, tty=True, socket=True)
            sock = cast(socket.socket, getattr(raw_socket, "_sock", raw_socket))
            process = DockerExecTerminalProcess(
                api_client=self._api_client,
                container=container,
                exec_id=exec_id,
                sock=sock,
                loop=asyncio.get_running_loop(),
            )
            await process.resize(cols, rows)
            return process
        except NotFound as exc:
            raise TerminalBackendUnavailableError("Runtime container is not available.") from exc
        except DockerException as exc:
            raise TerminalBackendUnavailableError("Failed to open Docker PTY session.") from exc

    async def shutdown(self) -> None:
        return None


class TerminalRuntimeService:
    def __init__(
        self,
        *,
        settings: Settings,
        database_engine: Engine,
        event_broker: SessionEventBroker,
        backend: TerminalBackend,
        registry: LiveTerminalRegistry,
        job_registry: LiveTerminalJobRegistry,
    ) -> None:
        self._settings = settings
        self._database_engine = database_engine
        self._event_broker = event_broker
        self._backend = backend
        self._registry = registry
        self._job_registry = job_registry

    async def _queue_frame(
        self,
        handle: LiveTerminalHandle,
        frame: dict[str, object],
        *,
        overflow_reason: str = "backpressure",
        best_effort: bool = False,
    ) -> bool:
        try:
            handle.queue.put_nowait(frame)
            return True
        except asyncio.QueueFull:
            if best_effort:
                try:
                    handle.queue.get_nowait()
                except asyncio.QueueEmpty:
                    return False
                try:
                    handle.queue.put_nowait(frame)
                except asyncio.QueueFull:
                    return False
                return True

            if handle.process is not None and not handle.finalized:
                asyncio.create_task(handle.process.close(reason=overflow_reason))
            return False

    @staticmethod
    def _append_tail(existing: str, chunk: str) -> str:
        merged = existing + chunk
        if len(merged) <= TERMINAL_JOB_OUTPUT_MAX_CHARS:
            return merged
        return merged[-TERMINAL_JOB_OUTPUT_MAX_CHARS:]

    @staticmethod
    def _append_output_buffer(existing: str, chunk: str) -> str:
        merged = existing + chunk
        if len(merged) <= TERMINAL_BUFFER_MAX_CHARS:
            return merged
        return merged[-TERMINAL_BUFFER_MAX_CHARS:]

    @staticmethod
    def _slice_tail_lines(content: str, lines: int) -> str:
        if not content:
            return ""
        tail_lines = content.splitlines()
        if len(tail_lines) <= lines:
            return "\n".join(tail_lines)
        return "\n".join(tail_lines[-lines:])

    async def _get_live_terminal_handle(
        self,
        *,
        session_id: str,
        terminal_id: str,
    ) -> LiveTerminalHandle:
        handle = await self._registry.get(terminal_id=terminal_id)
        if handle is None:
            raise TerminalNotAttachedError("Terminal is not currently attached")
        if handle.session_id != session_id:
            raise TerminalNotFoundError("Terminal not found")
        if handle.process is None or handle.finalized or handle.closed.is_set():
            raise TerminalClosedError("Terminal is already closed")
        if not handle.attached:
            raise TerminalNotAttachedError("Terminal is not currently attached")
        return handle

    async def send_terminal_input(
        self,
        *,
        session_id: str,
        terminal_id: str,
        data: str,
    ) -> None:
        handle = await self._get_live_terminal_handle(
            session_id=session_id, terminal_id=terminal_id
        )
        await self.handle_client_frame(handle, {"type": "input", "data": data})

    async def resize_terminal(
        self,
        *,
        session_id: str,
        terminal_id: str,
        cols: int,
        rows: int,
    ) -> None:
        handle = await self._get_live_terminal_handle(
            session_id=session_id, terminal_id=terminal_id
        )
        await self.handle_client_frame(handle, {"type": "resize", "cols": cols, "rows": rows})

    async def interrupt_terminal(
        self,
        *,
        session_id: str,
        terminal_id: str,
    ) -> None:
        handle = await self._get_live_terminal_handle(
            session_id=session_id, terminal_id=terminal_id
        )
        await self.handle_client_frame(handle, {"type": "interrupt"})

    async def execute_in_terminal(
        self,
        *,
        session_id: str,
        terminal_id: str,
        command: str,
        detach: bool,
        timeout_seconds: int,
        artifact_paths: list[str],
        runtime_policy: RuntimePolicy,
    ) -> tuple[str | None, str]:
        RuntimeService._enforce_runtime_policy(
            command=command,
            timeout_seconds=timeout_seconds,
            policy=runtime_policy,
        )
        if detach:
            job_id = await self.start_background_job(
                session_id=session_id,
                terminal_id=terminal_id,
                command=command,
                timeout_seconds=timeout_seconds,
                artifact_paths=artifact_paths,
            )
            return job_id, RuntimeTerminalJobStatus.RUNNING.value

        handle = await self._get_live_terminal_handle(
            session_id=session_id, terminal_id=terminal_id
        )
        await self.handle_client_frame(handle, {"type": "input", "data": f"{command}\n"})
        return handle.job_id, RuntimeTerminalJobStatus.RUNNING.value

    async def close_live_terminal(
        self,
        *,
        session_id: str,
        terminal_id: str,
        reason: str = "close",
    ) -> bool:
        handle = await self._registry.get(terminal_id=terminal_id)
        if handle is None or handle.session_id != session_id:
            return False

        if handle.process is not None and not handle.finalized:
            try:
                await handle.process.close(reason=reason)
            except Exception:
                await self._finalize_handle(handle, exit_code=None, reason="error")

        try:
            await asyncio.wait_for(handle.closed.wait(), timeout=TERMINAL_CLOSE_WAIT_SECONDS)
        except TimeoutError:
            await self._finalize_handle(handle, exit_code=None, reason=reason)

        return True

    async def emit_protocol_error(self, handle: LiveTerminalHandle, message: str) -> bool:
        return await self._queue_frame(
            handle,
            {"type": "error", "message": message},
            best_effort=True,
        )

    async def connect(
        self,
        *,
        session_id: str,
        terminal_id: str,
        cols: int,
        rows: int,
    ) -> LiveTerminalHandle:
        with DBSession(self._database_engine) as db_session:
            session_repository = SessionRepository(db_session)
            session = session_repository.get_session(session_id)
            if session is None:
                raise TerminalNotFoundError("Session not found")

            terminal_repository = TerminalRepository(db_session)
            terminal = terminal_repository.get_terminal_session(
                session_id=session_id,
                terminal_session_id=terminal_id,
            )
            if terminal is None:
                raise TerminalNotFoundError("Terminal not found")
            if terminal.closed_at is not None:
                raise TerminalClosedError("Terminal is already closed")
            shell = terminal.shell
            cwd = terminal.cwd

        async def _create_handle() -> LiveTerminalHandle:
            return LiveTerminalHandle(
                session_id=session_id,
                terminal_id=terminal_id,
                job_id="",
            )

        handle, created = await self._registry.acquire_or_create(
            session_id=session_id,
            terminal_id=terminal_id,
            create_handle=_create_handle,
        )

        if created:
            job_id: str | None = None
            try:
                with DBSession(self._database_engine) as db_session:
                    session_repository = SessionRepository(db_session)
                    started_session = session_repository.get_session(session_id)
                    if started_session is None:
                        raise TerminalNotFoundError("Session not found")

                    shell_service = SessionShellService(
                        TerminalRepository(db_session),
                        RunLogRepository(db_session),
                    )
                    job_result = shell_service.start_terminal_job(
                        session=started_session,
                        terminal_id=terminal_id,
                        command=shell,
                        metadata={"cwd": cwd, "cols": cols, "rows": rows},
                    )
                    job_id = job_result.job.id

                process = await self._backend.open_terminal(
                    terminal_id=terminal_id,
                    shell=shell,
                    cwd=cwd,
                    cols=cols,
                    rows=rows,
                )
            except Exception:
                await self._registry.remove(terminal_id=terminal_id)
                handle.attached = False
                if job_id is not None:
                    with DBSession(self._database_engine) as db_session:
                        session_repository = SessionRepository(db_session)
                        failed_session = session_repository.get_session(session_id)
                        if failed_session is not None:
                            shell_service = SessionShellService(
                                TerminalRepository(db_session),
                                RunLogRepository(db_session),
                            )
                            shell_service.finish_terminal_job(
                                session=failed_session,
                                job_id=job_id,
                                status=RuntimeTerminalJobStatus.FAILED,
                                exit_code=None,
                                reason="backend_open_failed",
                            )
                            shell_service.close_terminal(
                                session=failed_session,
                                terminal_id=terminal_id,
                                reason="backend_open_failed",
                                job_id=job_id,
                            )
                raise

            handle.job_id = job_id
            handle.process = process
            handle.pump_task = asyncio.create_task(
                self._pump_process_events(handle), name=f"pty:{terminal_id}"
            )
            await self._event_broker.publish(
                SessionEvent(
                    type=SessionEventType.TERMINAL_JOB_STARTED,
                    session_id=session_id,
                    payload={
                        "job_id": handle.job_id,
                        "terminal_id": terminal_id,
                        "session_id": session_id,
                        "status": RuntimeTerminalJobStatus.RUNNING.value,
                    },
                )
            )

        await self._queue_frame(
            handle,
            {
                "type": "ready",
                "session_id": session_id,
                "terminal_id": terminal_id,
                "job_id": handle.job_id,
                "reattached": not created,
            },
        )
        return handle

    async def _pump_process_events(self, handle: LiveTerminalHandle) -> None:
        assert handle.process is not None
        while True:
            event = await handle.process.events.get()
            if event.kind == "output":
                decoded = (event.data or b"").decode("utf-8", errors="replace")
                handle.output_buffer = self._append_output_buffer(handle.output_buffer, decoded)
                await self._queue_frame(
                    handle,
                    {
                        "type": "output",
                        "data": decoded,
                    },
                    overflow_reason="output_backpressure",
                )
                continue
            if event.kind == "error":
                await self._queue_frame(
                    handle,
                    {"type": "error", "message": event.message or "terminal error"},
                    best_effort=True,
                )
                await self._finalize_handle(handle, exit_code=None, reason="error")
                return
            if event.kind == "exit":
                await self._finalize_handle(
                    handle,
                    exit_code=event.exit_code,
                    reason=event.reason or "exit",
                )
                return

    async def handle_client_frame(
        self, handle: LiveTerminalHandle, frame: dict[str, object]
    ) -> None:
        if handle.process is None:
            raise TerminalRuntimeError("Terminal process is not available.")

        frame_type = frame.get("type")
        try:
            if frame_type == "input":
                data = frame.get("data")
                if not isinstance(data, str):
                    await self._queue_frame(
                        handle,
                        {"type": "error", "message": "input frame requires string data"},
                        best_effort=True,
                    )
                    return
                encoded_data = data.encode("utf-8")
                if len(encoded_data) > TERMINAL_CLIENT_FRAME_MAX_BYTES:
                    await self._queue_frame(
                        handle,
                        {"type": "error", "message": "input frame exceeds terminal size limit"},
                        best_effort=True,
                    )
                    return
                await handle.process.send_input(encoded_data)
                return
            if frame_type == "resize":
                cols = frame.get("cols")
                rows = frame.get("rows")
                if not isinstance(cols, int) or not isinstance(rows, int) or cols <= 0 or rows <= 0:
                    await self._queue_frame(
                        handle,
                        {
                            "type": "error",
                            "message": "resize frame requires positive cols and rows",
                        },
                        best_effort=True,
                    )
                    return
                await handle.process.resize(cols, rows)
                return
            if frame_type == "signal":
                signal_name = frame.get("signal")
                if not isinstance(signal_name, str) or not signal_name:
                    await self._queue_frame(
                        handle,
                        {"type": "error", "message": "signal frame requires a signal name"},
                        best_effort=True,
                    )
                    return
                await handle.process.send_signal(normalize_terminal_signal_name(signal_name))
                return
            if frame_type == "interrupt":
                await handle.process.send_signal("INT")
                return
            if frame_type == "eof":
                await handle.process.send_eof()
                return
            if frame_type == "close":
                await handle.process.close(reason="close")
                return
            await self._queue_frame(
                handle,
                {"type": "error", "message": "unsupported terminal frame type"},
                best_effort=True,
            )
        except TerminalRuntimeError as exc:
            await self._queue_frame(
                handle,
                {"type": "error", "message": str(exc)},
                best_effort=True,
            )
        except Exception:
            await self._queue_frame(
                handle,
                {"type": "error", "message": "terminal operation failed"},
                best_effort=True,
            )
            try:
                await handle.process.close(reason="error")
            except Exception:
                await self._finalize_handle(handle, exit_code=None, reason="error")

    async def start_background_job(
        self,
        *,
        session_id: str,
        terminal_id: str,
        command: str,
        timeout_seconds: int,
        artifact_paths: list[str],
    ) -> str:
        async with self._job_registry.terminal_start_lock(terminal_id):
            with DBSession(self._database_engine) as db_session:
                session_repository = SessionRepository(db_session)
                session = session_repository.get_session(session_id)
                if session is None:
                    raise TerminalNotFoundError("Session not found")

                terminal_repository = TerminalRepository(db_session)
                terminal = terminal_repository.get_terminal_session(
                    session_id=session_id,
                    terminal_session_id=terminal_id,
                )
                if terminal is None:
                    raise TerminalNotFoundError("Terminal not found")
                if terminal.closed_at is not None:
                    raise TerminalClosedError("Terminal is already closed")

                shell_service = SessionShellService(
                    terminal_repository,
                    RunLogRepository(db_session),
                )
                job_result = shell_service.start_terminal_job(
                    session=session,
                    terminal_id=terminal_id,
                    command=command,
                    metadata={
                        "detach": True,
                        "timeout_seconds": timeout_seconds,
                        "artifact_paths": list(artifact_paths),
                        "stdout_tail": "",
                        "stderr_tail": "",
                    },
                    detached_conflict_only=True,
                )
                if not job_result.changed:
                    raise TerminalJobAlreadyRunningError(
                        "Terminal already has a running detached job"
                    )
                job_id = job_result.job.id
                shell = terminal.shell
                cwd = terminal.cwd

            started_at = utc_now()
            handle = LiveTerminalJobHandle(
                session_id=session_id,
                terminal_id=terminal_id,
                job_id=job_id,
                command=command,
                timeout_seconds=timeout_seconds,
                artifact_paths=list(artifact_paths),
                started_at=started_at,
            )
            await self._job_registry.put(handle)
            try:
                process = await self._backend.open_terminal(
                    terminal_id=f"{terminal_id}:job:{job_id}",
                    shell=shell,
                    cwd=cwd,
                    cols=80,
                    rows=24,
                )
                handle.process = process
                handle.pump_task = asyncio.create_task(
                    self._pump_background_job_events(handle),
                    name=f"pty-job:{job_id}",
                )
                self._schedule_background_job_timeout(handle)
                await process.send_input(f"{command}\nexit\n".encode())
            except Exception:
                await self._job_registry.remove(job_id=job_id)
                with DBSession(self._database_engine) as db_session:
                    session_repository = SessionRepository(db_session)
                    failed_session = session_repository.get_session(session_id)
                    if failed_session is not None:
                        shell_service = SessionShellService(
                            TerminalRepository(db_session),
                            RunLogRepository(db_session),
                        )
                        shell_service.finish_terminal_job(
                            session=failed_session,
                            job_id=job_id,
                            status=RuntimeTerminalJobStatus.FAILED,
                            exit_code=None,
                            reason="backend_open_failed",
                        )
                await self._event_broker.publish(
                    SessionEvent(
                        type=SessionEventType.TERMINAL_JOB_FAILED,
                        session_id=session_id,
                        payload={
                            "job_id": job_id,
                            "terminal_id": terminal_id,
                            "session_id": session_id,
                            "status": RuntimeTerminalJobStatus.FAILED.value,
                            "reason": "backend_open_failed",
                        },
                    )
                )
                raise

            await self._event_broker.publish(
                SessionEvent(
                    type=SessionEventType.TERMINAL_JOB_STARTED,
                    session_id=session_id,
                    payload={
                        "job_id": job_id,
                        "terminal_id": terminal_id,
                        "session_id": session_id,
                        "status": RuntimeTerminalJobStatus.RUNNING.value,
                        "command": command,
                        "detach": True,
                    },
                )
            )
            return job_id

    async def _pump_background_job_events(self, handle: LiveTerminalJobHandle) -> None:
        assert handle.process is not None
        while True:
            event = await handle.process.events.get()
            if event.kind == "output":
                chunk = (event.data or b"").decode("utf-8", errors="replace")
                handle.stdout_tail = self._append_tail(handle.stdout_tail, chunk)
                continue
            if event.kind == "error":
                handle.stderr_tail = self._append_tail(
                    handle.stderr_tail,
                    event.message or "terminal error",
                )
                await self._finalize_background_job(handle, exit_code=None, reason="error")
                return
            if event.kind == "exit":
                await self._finalize_background_job(
                    handle,
                    exit_code=event.exit_code,
                    reason=event.reason or "exit",
                )
                return

    def _schedule_background_job_timeout(self, handle: LiveTerminalJobHandle) -> None:
        if handle.timeout_seconds <= 0:
            return

        async def _timeout_close() -> None:
            if handle.finalized or handle.process is None:
                return
            await handle.process.close(reason="timeout")

        def _spawn_timeout_close() -> None:
            handle.timeout_handle = None
            handle.timeout_task = asyncio.create_task(
                _timeout_close(),
                name=f"pty-job-timeout:{handle.job_id}",
            )

        handle.timeout_handle = asyncio.get_running_loop().call_later(
            handle.timeout_seconds,
            _spawn_timeout_close,
        )

    async def stop_background_job(self, *, session_id: str, job_id: str) -> bool:
        handle = await self._job_registry.get(job_id=job_id)
        if handle is None or handle.session_id != session_id:
            return False
        if handle.process is not None and not handle.finalized:
            await handle.process.close(reason="close")
        try:
            await asyncio.wait_for(handle.closed.wait(), timeout=TERMINAL_CLOSE_WAIT_SECONDS)
        except TimeoutError:
            await self._finalize_background_job(handle, exit_code=None, reason="close")
        return True

    async def get_background_job_tail(
        self,
        *,
        session_id: str,
        job_id: str,
        stream: Literal["stdout", "stderr"],
        lines: int,
    ) -> str | None:
        handle = await self._job_registry.get(job_id=job_id)
        if handle is None:
            return None
        if handle.session_id != session_id:
            raise TerminalNotFoundError("Terminal job not found")
        content = handle.stderr_tail if stream == "stderr" else handle.stdout_tail
        return self._slice_tail_lines(content, lines)

    async def get_active_background_job_ids(self, *, session_id: str) -> set[str]:
        return await self._job_registry.active_job_ids_for_session(session_id=session_id)

    async def get_terminal_runtime_snapshots(
        self, *, session_id: str
    ) -> dict[str, TerminalRuntimeSnapshot]:
        handles = await self._registry.list_handles()
        return {
            handle.terminal_id: TerminalRuntimeSnapshot(
                terminal_id=handle.terminal_id,
                attached=handle.attached and not handle.closed.is_set() and not handle.finalized,
                active_job_id=handle.job_id,
                reattach_deadline=handle.reattach_deadline,
            )
            for handle in handles
            if handle.session_id == session_id and not handle.finalized
        }

    async def get_terminal_buffer(
        self,
        *,
        session_id: str,
        terminal_id: str,
        lines: int,
    ) -> tuple[str, bool, str | None, datetime | None] | None:
        handle = await self._registry.get(terminal_id=terminal_id)
        if handle is None:
            return None
        if handle.session_id != session_id:
            raise TerminalNotFoundError("Terminal not found")
        if handle.finalized or handle.closed.is_set():
            return None
        return (
            self._slice_tail_lines(handle.output_buffer, lines),
            handle.attached,
            handle.job_id,
            handle.reattach_deadline,
        )

    async def mark_detached(
        self, handle: LiveTerminalHandle, *, timeout_seconds: float | None = None
    ) -> None:
        handle.attached = False
        handle.detach_generation += 1
        current_generation = handle.detach_generation
        if handle.finalized:
            return
        if handle.detach_task is not None:
            handle.detach_task.cancel()
            handle.detach_task = None
        if handle.detach_timer is not None:
            handle.detach_timer.cancel()
            handle.detach_timer = None

        async def _timeout_close() -> None:
            if (
                handle.attached
                or handle.finalized
                or handle.process is None
                or handle.detach_generation != current_generation
            ):
                return
            await handle.process.close(reason="disconnect_timeout")

        def _spawn_timeout_close() -> None:
            handle.detach_timer = None
            handle.detach_task = asyncio.create_task(
                _timeout_close(),
                name=f"pty-detach:{handle.terminal_id}",
            )

        delay = (
            self._settings.terminal_disconnect_grace_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        handle.reattach_deadline = utc_now() + timedelta(seconds=max(delay, 0))
        handle.detach_timer = asyncio.get_running_loop().call_later(delay, _spawn_timeout_close)

    def _resolve_terminal_artifacts(
        self,
        *,
        artifact_paths: list[str],
    ) -> list[tuple[str, str, str]]:
        workspace_dir = Path(self._settings.runtime_workspace_dir).resolve()
        workspace_container_path = PurePosixPath(self._settings.runtime_workspace_container_path)
        normalized: list[tuple[str, str, str]] = []
        seen: set[str] = set()

        for artifact_path in artifact_paths:
            cleaned = artifact_path.strip()
            if not cleaned:
                continue
            relative_path = cleaned.replace("\\", "/").lstrip("/")
            if relative_path in seen:
                continue
            seen.add(relative_path)
            host_path = (workspace_dir / Path(relative_path)).resolve()
            if not host_path.exists() or not host_path.is_relative_to(workspace_dir):
                continue
            normalized.append(
                (
                    relative_path,
                    str(host_path),
                    str(workspace_container_path / PurePosixPath(relative_path)),
                )
            )
        return normalized

    @staticmethod
    def _resolve_execution_status(*, reason: str, exit_code: int | None) -> ExecutionStatus:
        if reason == "timeout":
            return ExecutionStatus.TIMEOUT
        if exit_code == 0:
            return ExecutionStatus.SUCCESS
        return ExecutionStatus.FAILED

    async def _finalize_background_job(
        self,
        handle: LiveTerminalJobHandle,
        *,
        exit_code: int | None,
        reason: str,
    ) -> None:
        async with handle.finalize_lock:
            if handle.finalized:
                return
            handle.finalized = True
            if handle.timeout_task is not None:
                handle.timeout_task.cancel()
                handle.timeout_task = None
            if handle.timeout_handle is not None:
                handle.timeout_handle.cancel()
                handle.timeout_handle = None

            status = self._resolve_terminal_job_status(reason=reason, exit_code=exit_code)
            execution_status = self._resolve_execution_status(reason=reason, exit_code=exit_code)
            artifacts = self._resolve_terminal_artifacts(artifact_paths=handle.artifact_paths)
            stdout_tail = handle.stdout_tail
            stderr_tail = handle.stderr_tail
            metadata_updates = {
                "stdout_tail": stdout_tail,
                "stderr_tail": stderr_tail,
                "artifact_paths": [artifact[0] for artifact in artifacts],
                "finish_reason": reason,
            }

            job_result = None
            run_id: str | None = None
            with DBSession(self._database_engine) as db_session:
                session_repository = SessionRepository(db_session)
                session = session_repository.get_session(handle.session_id)
                if session is not None:
                    runtime_repository = RuntimeRepository(db_session)
                    run_log_repository = RunLogRepository(db_session)
                    run, _artifact_rows = runtime_repository.create_run(
                        session_id=handle.session_id,
                        command=handle.command,
                        requested_timeout_seconds=handle.timeout_seconds,
                        status=execution_status,
                        exit_code=exit_code,
                        stdout=stdout_tail,
                        stderr=stderr_tail,
                        container_name=self._settings.runtime_container_name,
                        started_at=handle.started_at,
                        ended_at=utc_now(),
                        artifacts=artifacts,
                    )
                    run_id = run.id
                    shell_service = SessionShellService(
                        TerminalRepository(db_session), run_log_repository
                    )
                    job_result = shell_service.finish_terminal_job(
                        session=session,
                        job_id=handle.job_id,
                        status=status,
                        exit_code=exit_code,
                        reason=reason,
                        metadata_updates={**metadata_updates, "run_id": run_id},
                    )

            if job_result is not None and job_result.changed:
                event_type = {
                    RuntimeTerminalJobStatus.COMPLETED: SessionEventType.TERMINAL_JOB_COMPLETED,
                    RuntimeTerminalJobStatus.FAILED: SessionEventType.TERMINAL_JOB_FAILED,
                    RuntimeTerminalJobStatus.CANCELLED: SessionEventType.TERMINAL_JOB_CANCELLED,
                    RuntimeTerminalJobStatus.TIMEOUT: SessionEventType.TERMINAL_JOB_FAILED,
                }[status]
                payload = terminal_job_audit_payload(job_result.job, reason=reason)
                payload["run_id"] = run_id
                await self._event_broker.publish(
                    SessionEvent(
                        type=event_type,
                        session_id=handle.session_id,
                        payload=payload,
                    )
                )

            handle.closed.set()
            await self._job_registry.remove(job_id=handle.job_id)

    async def shutdown(self) -> None:
        handles = await self._registry.list_handles()
        close_tasks = [
            asyncio.create_task(handle.process.close(reason="shutdown"))
            for handle in handles
            if handle.process is not None and not handle.finalized
        ]
        background_job_handles = await self._job_registry.list_handles()
        close_tasks.extend(
            asyncio.create_task(handle.process.close(reason="shutdown"))
            for handle in background_job_handles
            if handle.process is not None and not handle.finalized
        )
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        await self._backend.shutdown()

    async def _finalize_handle(
        self,
        handle: LiveTerminalHandle,
        *,
        exit_code: int | None,
        reason: str,
    ) -> None:
        async with handle.finalize_lock:
            if handle.finalized:
                return
            handle.finalized = True
            handle.reattach_deadline = None
            if handle.detach_task is not None:
                handle.detach_task.cancel()
                handle.detach_task = None
            if handle.detach_timer is not None:
                handle.detach_timer.cancel()
                handle.detach_timer = None

            status = self._resolve_terminal_job_status(reason=reason, exit_code=exit_code)

            with DBSession(self._database_engine) as db_session:
                session_repository = SessionRepository(db_session)
                session = session_repository.get_session(handle.session_id)
                if session is None:
                    await self._registry.remove(terminal_id=handle.terminal_id)
                    handle.closed.set()
                    return

                terminal_repository = TerminalRepository(db_session)
                shell_service = SessionShellService(
                    terminal_repository, RunLogRepository(db_session)
                )
                job_result = shell_service.finish_terminal_job(
                    session=session,
                    job_id=handle.job_id,
                    status=status,
                    exit_code=exit_code,
                    reason=reason,
                )
                terminal_result = shell_service.close_terminal(
                    session=session,
                    terminal_id=handle.terminal_id,
                    reason=reason,
                    exit_code=exit_code,
                    job_id=handle.job_id,
                )

            if job_result is not None and job_result.changed:
                event_type = {
                    RuntimeTerminalJobStatus.COMPLETED: SessionEventType.TERMINAL_JOB_COMPLETED,
                    RuntimeTerminalJobStatus.FAILED: SessionEventType.TERMINAL_JOB_FAILED,
                    RuntimeTerminalJobStatus.CANCELLED: SessionEventType.TERMINAL_JOB_CANCELLED,
                    RuntimeTerminalJobStatus.TIMEOUT: SessionEventType.TERMINAL_JOB_FAILED,
                }[status]
                await self._event_broker.publish(
                    SessionEvent(
                        type=event_type,
                        session_id=handle.session_id,
                        payload=terminal_job_audit_payload(job_result.job, reason=reason),
                    )
                )
            if terminal_result is not None and terminal_result.changed:
                await self._event_broker.publish(
                    SessionEvent(
                        type=SessionEventType.TERMINAL_SESSION_CLOSED,
                        session_id=handle.session_id,
                        payload=terminal_audit_payload(
                            terminal_result.terminal,
                            reason=reason,
                            exit_code=exit_code,
                            job_id=handle.job_id,
                        ),
                    )
                )

            await self._queue_frame(
                handle,
                {"type": "exit", "exit_code": exit_code, "reason": reason},
                best_effort=True,
            )
            await self._queue_frame(
                handle,
                {"type": "closed", "reason": reason},
                best_effort=True,
            )
            handle.closed.set()
            await self._registry.remove(terminal_id=handle.terminal_id)

    @staticmethod
    def _resolve_terminal_job_status(
        *, reason: str, exit_code: int | None
    ) -> RuntimeTerminalJobStatus:
        if reason == "timeout":
            return RuntimeTerminalJobStatus.TIMEOUT
        if reason in {"close", "disconnect_timeout", "shutdown", "eof"}:
            return RuntimeTerminalJobStatus.CANCELLED
        if reason == "error":
            return RuntimeTerminalJobStatus.FAILED
        if exit_code == 0:
            return RuntimeTerminalJobStatus.COMPLETED
        return RuntimeTerminalJobStatus.FAILED


def get_live_terminal_registry(app: FastAPI) -> LiveTerminalRegistry:
    registry = getattr(app.state, "live_terminal_registry", None)
    if isinstance(registry, LiveTerminalRegistry):
        return registry
    registry = LiveTerminalRegistry()
    app.state.live_terminal_registry = registry
    return registry


def get_live_terminal_job_registry(app: FastAPI) -> LiveTerminalJobRegistry:
    registry = getattr(app.state, "live_terminal_job_registry", None)
    if isinstance(registry, LiveTerminalJobRegistry):
        return registry
    registry = LiveTerminalJobRegistry()
    app.state.live_terminal_job_registry = registry
    return registry


def get_terminal_backend(app: FastAPI) -> TerminalBackend:
    backend = getattr(app.state, "terminal_backend", None)
    if backend is not None:
        return cast(TerminalBackend, backend)
    backend = DockerTerminalBackend(app.state.settings)
    app.state.terminal_backend = backend
    return backend


async def recover_orphaned_terminal_state(
    *,
    database_engine: Engine,
    settings: Settings,
) -> OrphanTerminalRecoveryResult:
    result = OrphanTerminalRecoveryResult()

    with DBSession(database_engine) as db_session:
        terminal_repository = TerminalRepository(db_session)
        running_jobs = terminal_repository.list_running_terminal_jobs()
        open_terminals = terminal_repository.list_open_terminal_sessions()

    if not running_jobs and not open_terminals:
        return result

    runtime_backend = get_runtime_backend(settings)
    try:
        runtime_backend.stop()
    except Exception:
        pass
    finally:
        result.runtime_stop_attempted = True

    with DBSession(database_engine) as db_session:
        session_repository = SessionRepository(db_session)
        shell_service = SessionShellService(
            TerminalRepository(db_session),
            RunLogRepository(db_session),
        )

        cancelled_job_ids: set[str] = set()
        for job in running_jobs:
            session = session_repository.get_session(job.session_id, include_deleted=True)
            if session is None:
                continue
            job_result = shell_service.finish_terminal_job(
                session=session,
                job_id=job.id,
                status=RuntimeTerminalJobStatus.CANCELLED,
                exit_code=None,
                reason="service_restart",
                metadata_updates={
                    "finish_reason": "service_restart",
                    "recovered_on_startup": True,
                },
            )
            if job_result is not None and job_result.changed:
                cancelled_job_ids.add(job.id)

        closed_terminal_ids: set[str] = set()
        for terminal in open_terminals:
            session = session_repository.get_session(terminal.session_id, include_deleted=True)
            if session is None:
                continue
            terminal_result = shell_service.close_terminal(
                session=session,
                terminal_id=terminal.id,
                reason="service_restart",
            )
            if terminal_result is not None and terminal_result.changed:
                closed_terminal_ids.add(terminal.id)

        result.cancelled_jobs = len(cancelled_job_ids)
        result.closed_terminals = len(closed_terminal_ids)

    return result


def build_terminal_runtime_service(
    *,
    app: FastAPI,
    event_broker: SessionEventBroker,
) -> TerminalRuntimeService:
    return TerminalRuntimeService(
        settings=app.state.settings,
        database_engine=app.state.database_engine,
        event_broker=event_broker,
        backend=get_terminal_backend(app),
        registry=get_live_terminal_registry(app),
        job_registry=get_live_terminal_job_registry(app),
    )
