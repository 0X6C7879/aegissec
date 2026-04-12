import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session as DBSession

import app.services.terminal_runtime as terminal_runtime
from app.core.events import SessionEventBroker
from app.db.models import RuntimeTerminalJobStatus
from app.db.repositories import TerminalRepository
from app.main import app
from app.services.terminal_runtime import (
    LiveTerminalHandle,
    LiveTerminalJobRegistry,
    LiveTerminalRegistry,
    TerminalAlreadyAttachedError,
    TerminalJobAlreadyRunningError,
    TerminalNotFoundError,
    TerminalRuntimeService,
)
from tests.utils import api_data


def _create_session_and_terminal(
    client: TestClient, *, title: str = "Runtime Terminal"
) -> tuple[str, str]:
    session_response = client.post("/api/sessions", json={"title": title})
    session_id = api_data(session_response)["id"]
    terminal_response = client.post(
        f"/api/sessions/{session_id}/terminals",
        json={"title": f"{title} Shell"},
    )
    terminal_id = api_data(terminal_response)["id"]
    return session_id, terminal_id


def _build_service() -> TerminalRuntimeService:
    return TerminalRuntimeService(
        settings=app.state.settings,
        database_engine=app.state.database_engine,
        event_broker=SessionEventBroker(),
        backend=app.state.terminal_backend,
        registry=LiveTerminalRegistry(),
        job_registry=LiveTerminalJobRegistry(),
    )


@pytest.mark.anyio
async def test_live_terminal_registry_rejects_second_attach_for_same_terminal() -> None:
    registry = LiveTerminalRegistry()

    handle = LiveTerminalHandle(session_id="session-1", terminal_id="terminal-1", job_id="job-1")
    attached = await registry.attach(terminal_id="terminal-1", handle=handle)

    assert attached is handle
    with pytest.raises(TerminalAlreadyAttachedError):
        await registry.attach(
            terminal_id="terminal-1",
            handle=LiveTerminalHandle(
                session_id="session-1",
                terminal_id="terminal-1",
                job_id="job-2",
            ),
        )

    second = LiveTerminalHandle(session_id="session-1", terminal_id="terminal-2", job_id="job-3")
    assert await registry.attach(terminal_id="terminal-2", handle=second) is second


@pytest.mark.anyio
async def test_terminal_runtime_service_marks_natural_exit_completed(client: TestClient) -> None:
    session_id, terminal_id = _create_session_and_terminal(client, title="Natural Exit")
    service = _build_service()

    handle = await service.connect(session_id=session_id, terminal_id=terminal_id, cols=80, rows=24)
    assert handle.process is not None
    await handle.process.send_input(b"exit\n")
    await asyncio.wait_for(handle.closed.wait(), timeout=1)

    with DBSession(app.state.database_engine) as db_session:
        repository = TerminalRepository(db_session)
        jobs = repository.list_terminal_jobs(session_id=session_id, terminal_session_id=terminal_id)
        assert len(jobs) == 1
        assert jobs[0].status == RuntimeTerminalJobStatus.COMPLETED
        assert jobs[0].exit_code == 0


@pytest.mark.anyio
async def test_terminal_runtime_service_disconnect_timeout_cancels_job_and_closes_terminal(
    client: TestClient,
) -> None:
    session_id, terminal_id = _create_session_and_terminal(client, title="Disconnect Timeout")
    service = _build_service()

    handle = await service.connect(session_id=session_id, terminal_id=terminal_id, cols=80, rows=24)
    await service.mark_detached(handle, timeout_seconds=0.01)
    await asyncio.wait_for(handle.closed.wait(), timeout=1)

    with DBSession(app.state.database_engine) as db_session:
        terminal_repository = TerminalRepository(db_session)
        job = terminal_repository.list_terminal_jobs(
            session_id=session_id,
            terminal_session_id=terminal_id,
        )[0]
        terminal = terminal_repository.get_terminal_session(
            session_id=session_id,
            terminal_session_id=terminal_id,
        )

        assert job.status == RuntimeTerminalJobStatus.CANCELLED
        assert terminal is not None
        assert terminal.closed_at is not None


@pytest.mark.anyio
async def test_terminal_runtime_service_reattach_within_grace_reuses_same_job(
    client: TestClient,
) -> None:
    session_id, terminal_id = _create_session_and_terminal(client, title="Reattach During Grace")
    service = _build_service()

    first_handle = await service.connect(
        session_id=session_id, terminal_id=terminal_id, cols=80, rows=24
    )
    first_ready = await asyncio.wait_for(first_handle.queue.get(), timeout=1)
    assert first_ready["type"] == "ready"

    await service.mark_detached(first_handle, timeout_seconds=0.05)
    second_handle = await service.connect(
        session_id=session_id, terminal_id=terminal_id, cols=80, rows=24
    )
    second_ready = await asyncio.wait_for(second_handle.queue.get(), timeout=1)

    assert second_handle is first_handle
    assert second_ready["reattached"] is True
    assert second_ready["job_id"] == first_ready["job_id"]

    with DBSession(app.state.database_engine) as db_session:
        jobs = TerminalRepository(db_session).list_terminal_jobs(
            session_id=session_id,
            terminal_session_id=terminal_id,
        )
        assert len(jobs) == 1


@pytest.mark.anyio
async def test_terminal_runtime_service_shutdown_closes_live_handles(client: TestClient) -> None:
    session_id, terminal_id = _create_session_and_terminal(client, title="Shutdown Cleanup")
    service = _build_service()

    handle = await service.connect(session_id=session_id, terminal_id=terminal_id, cols=80, rows=24)
    await service.shutdown()
    await asyncio.wait_for(handle.closed.wait(), timeout=1)

    with DBSession(app.state.database_engine) as db_session:
        repository = TerminalRepository(db_session)
        job = repository.list_terminal_jobs(
            session_id=session_id,
            terminal_session_id=terminal_id,
        )[0]
        assert job.status == RuntimeTerminalJobStatus.CANCELLED

    assert await service._registry.list_handles() == []


@pytest.mark.anyio
async def test_terminal_runtime_service_rejects_concurrent_attach_without_second_job_leak(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id, terminal_id = _create_session_and_terminal(
        client, title="Concurrent Initial Attach"
    )
    service = _build_service()
    backend = app.state.terminal_backend

    started = asyncio.Event()
    release = asyncio.Event()
    open_calls = 0
    original_open_terminal = backend.open_terminal

    async def delayed_open_terminal(**kwargs: object) -> object:
        nonlocal open_calls
        open_calls += 1
        started.set()
        await release.wait()
        return await original_open_terminal(**kwargs)

    monkeypatch.setattr(backend, "open_terminal", delayed_open_terminal)

    first_task = asyncio.create_task(
        service.connect(session_id=session_id, terminal_id=terminal_id, cols=80, rows=24)
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    with pytest.raises(TerminalAlreadyAttachedError):
        await service.connect(session_id=session_id, terminal_id=terminal_id, cols=80, rows=24)

    release.set()
    handle = await asyncio.wait_for(first_task, timeout=1)

    assert open_calls == 1
    with DBSession(app.state.database_engine) as db_session:
        jobs = TerminalRepository(db_session).list_terminal_jobs(
            session_id=session_id,
            terminal_session_id=terminal_id,
        )
        assert len(jobs) == 1

    await service.shutdown()
    await asyncio.wait_for(handle.closed.wait(), timeout=1)


@pytest.mark.anyio
async def test_terminal_runtime_service_rejects_detached_handle_reuse_from_foreign_session(
    client: TestClient,
) -> None:
    owner_session_id, terminal_id = _create_session_and_terminal(client, title="Owner Session")
    foreign_session_response = client.post("/api/sessions", json={"title": "Foreign Session"})
    foreign_session_id = api_data(foreign_session_response)["id"]
    service = _build_service()

    handle = await service.connect(
        session_id=owner_session_id, terminal_id=terminal_id, cols=80, rows=24
    )
    await service.mark_detached(handle, timeout_seconds=0.05)

    with pytest.raises(TerminalNotFoundError):
        await service.connect(
            session_id=foreign_session_id, terminal_id=terminal_id, cols=80, rows=24
        )

    with DBSession(app.state.database_engine) as db_session:
        jobs = TerminalRepository(db_session).list_terminal_jobs(
            session_id=owner_session_id,
            terminal_session_id=terminal_id,
        )
        assert len(jobs) == 1

    await service.shutdown()
    await asyncio.wait_for(handle.closed.wait(), timeout=1)


@pytest.mark.anyio
async def test_terminal_runtime_service_serializes_concurrent_detached_start_per_terminal(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id, terminal_id = _create_session_and_terminal(
        client, title="Concurrent Detached Start"
    )
    service = _build_service()
    backend = app.state.terminal_backend

    started = asyncio.Event()
    release = asyncio.Event()
    open_calls = 0
    original_open_terminal = backend.open_terminal

    class HangingTerminalProcess:
        def __init__(self) -> None:
            self.events: asyncio.Queue[object] = asyncio.Queue()

        async def send_input(self, data: bytes) -> None:
            del data

        async def resize(self, cols: int, rows: int) -> None:
            del cols, rows

        async def send_signal(self, signal_name: str) -> None:
            del signal_name

        async def send_eof(self) -> None:
            await self.events.put(
                terminal_runtime.TerminalBackendEvent.exit(exit_code=0, reason="eof")
            )

        async def close(self, *, reason: str) -> None:
            await self.events.put(
                terminal_runtime.TerminalBackendEvent.exit(exit_code=None, reason=reason)
            )

    async def delayed_open_terminal(**kwargs: object) -> object:
        nonlocal open_calls
        terminal_key = kwargs.get("terminal_id")
        if isinstance(terminal_key, str) and ":job:" in terminal_key:
            open_calls += 1
            started.set()
            await release.wait()
            return HangingTerminalProcess()
        return await original_open_terminal(**kwargs)

    monkeypatch.setattr(backend, "open_terminal", delayed_open_terminal)

    first_task = asyncio.create_task(
        service.start_background_job(
            session_id=session_id,
            terminal_id=terminal_id,
            command="sleep 60",
            timeout_seconds=60,
            artifact_paths=[],
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    second_task = asyncio.create_task(
        service.start_background_job(
            session_id=session_id,
            terminal_id=terminal_id,
            command="sleep 30",
            timeout_seconds=60,
            artifact_paths=[],
        )
    )
    await asyncio.sleep(0)
    release.set()
    first_job_id = await asyncio.wait_for(first_task, timeout=1)

    with pytest.raises(TerminalJobAlreadyRunningError):
        await asyncio.wait_for(second_task, timeout=1)

    assert open_calls == 1
    with DBSession(app.state.database_engine) as db_session:
        jobs = TerminalRepository(db_session).list_terminal_jobs(
            session_id=session_id,
            terminal_session_id=terminal_id,
        )
        assert len(jobs) == 1
        assert jobs[0].id == first_job_id

    await service.shutdown()
