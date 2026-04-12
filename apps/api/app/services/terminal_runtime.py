from __future__ import annotations

import asyncio
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from threading import Thread
from typing import Protocol, cast

import docker
from docker.errors import DockerException, NotFound
from docker.models.containers import Container
from fastapi import FastAPI
from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession

from app.core.events import SessionEvent, SessionEventBroker, SessionEventType
from app.core.settings import Settings
from app.db.models import RuntimeTerminalJobStatus
from app.db.repositories import RunLogRepository, SessionRepository, TerminalRepository
from app.services.runtime import DockerRuntimeBackend, RuntimeOperationError
from app.services.terminal_sessions import (
    SessionShellService,
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


TERMINAL_CLIENT_FRAME_MAX_BYTES = 16 * 1024
TERMINAL_CLIENT_QUEUE_MAXSIZE = 256
TERMINAL_BACKEND_EVENT_QUEUE_MAXSIZE = 256
TERMINAL_ALLOWED_SIGNALS = frozenset({"HUP", "INT", "KILL", "QUIT", "TERM"})
TERMINAL_CLOSE_WAIT_SECONDS = 1.0


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
                if existing.detach_task is not None:
                    existing.detach_task.cancel()
                    existing.detach_task = None
                if existing.detach_timer is not None:
                    existing.detach_timer.cancel()
                    existing.detach_timer = None
                return existing, False

            handle.attached = True
            handle.detach_generation += 1
            self._handles[terminal_id] = handle
            return handle, True

    async def remove(self, *, terminal_id: str) -> None:
        async with self._lock:
            self._handles.pop(terminal_id, None)

    async def list_handles(self) -> list[LiveTerminalHandle]:
        async with self._lock:
            return list(self._handles.values())


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
    ) -> None:
        self._settings = settings
        self._database_engine = database_engine
        self._event_broker = event_broker
        self._backend = backend
        self._registry = registry

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
                await self._queue_frame(
                    handle,
                    {
                        "type": "output",
                        "data": (event.data or b"").decode("utf-8", errors="replace"),
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
        handle.detach_timer = asyncio.get_running_loop().call_later(delay, _spawn_timeout_close)

    async def shutdown(self) -> None:
        handles = await self._registry.list_handles()
        close_tasks = [
            asyncio.create_task(handle.process.close(reason="shutdown"))
            for handle in handles
            if handle.process is not None and not handle.finalized
        ]
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


def get_terminal_backend(app: FastAPI) -> TerminalBackend:
    backend = getattr(app.state, "terminal_backend", None)
    if backend is not None:
        return cast(TerminalBackend, backend)
    backend = DockerTerminalBackend(app.state.settings)
    app.state.terminal_backend = backend
    return backend


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
    )
