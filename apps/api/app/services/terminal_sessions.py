from __future__ import annotations

from dataclasses import dataclass

from app.core.events import SessionEventType
from app.db.models import (
    Session,
    TerminalJobRead,
    TerminalSessionCreateRequest,
    TerminalSessionRead,
    to_terminal_job_read,
    to_terminal_session_read,
)
from app.db.repositories import RunLogRepository, TerminalRepository

TERMINAL_RUN_LOG_SOURCE = "terminal"
TERMINAL_CREATED_EVENT_TYPE = SessionEventType.TERMINAL_SESSION_CREATED.value
TERMINAL_CLOSED_EVENT_TYPE = SessionEventType.TERMINAL_SESSION_CLOSED.value


@dataclass(slots=True)
class TerminalSessionMutationResult:
    terminal: TerminalSessionRead
    changed: bool


def terminal_audit_payload(terminal: TerminalSessionRead) -> dict[str, object]:
    return {
        "id": terminal.id,
        "session_id": terminal.session_id,
        "title": terminal.title,
        "status": terminal.status.value,
        "created_at": terminal.created_at.isoformat(),
        "updated_at": terminal.updated_at.isoformat(),
        "closed_at": terminal.closed_at.isoformat() if terminal.closed_at is not None else None,
    }


class SessionShellService:
    def __init__(
        self,
        terminal_repository: TerminalRepository,
        run_log_repository: RunLogRepository,
    ) -> None:
        self._terminal_repository = terminal_repository
        self._run_log_repository = run_log_repository

    def list_terminals(self, *, session_id: str) -> list[TerminalSessionRead]:
        terminals = self._terminal_repository.list_terminal_sessions(session_id=session_id)
        return [to_terminal_session_read(terminal) for terminal in terminals]

    def get_terminal(self, *, session_id: str, terminal_id: str) -> TerminalSessionRead | None:
        terminal = self._terminal_repository.get_terminal_session(
            session_id=session_id,
            terminal_session_id=terminal_id,
        )
        if terminal is None:
            return None
        return to_terminal_session_read(terminal)

    def create_terminal(
        self,
        *,
        session: Session,
        payload: TerminalSessionCreateRequest,
    ) -> TerminalSessionMutationResult:
        db_session = self._terminal_repository.db_session
        try:
            terminal = self._terminal_repository.create_terminal_session(
                session_id=session.id,
                title=payload.title or "Terminal",
                shell=payload.shell,
                cwd=payload.cwd,
                metadata=dict(payload.metadata_payload),
                commit=False,
            )
            terminal_read = to_terminal_session_read(terminal)
            self._run_log_repository.create_log(
                session_id=session.id,
                project_id=session.project_id,
                run_id=None,
                level="info",
                source=TERMINAL_RUN_LOG_SOURCE,
                event_type=TERMINAL_CREATED_EVENT_TYPE,
                message=terminal_read.title,
                payload=terminal_audit_payload(terminal_read),
                commit=False,
            )
            db_session.commit()
            db_session.refresh(terminal)
        except Exception:
            db_session.rollback()
            raise

        terminal_read = to_terminal_session_read(terminal)
        return TerminalSessionMutationResult(terminal=terminal_read, changed=True)

    def close_terminal(
        self,
        *,
        session: Session,
        terminal_id: str,
    ) -> TerminalSessionMutationResult | None:
        existing = self._terminal_repository.get_terminal_session(
            session_id=session.id,
            terminal_session_id=terminal_id,
        )
        if existing is None:
            return None

        changed = existing.closed_at is None
        if not changed:
            return TerminalSessionMutationResult(
                terminal=to_terminal_session_read(existing), changed=False
            )

        db_session = self._terminal_repository.db_session
        try:
            terminal = self._terminal_repository.close_terminal_session(existing, commit=False)
            terminal_read = to_terminal_session_read(terminal)
            self._run_log_repository.create_log(
                session_id=session.id,
                project_id=session.project_id,
                run_id=None,
                level="info",
                source=TERMINAL_RUN_LOG_SOURCE,
                event_type=TERMINAL_CLOSED_EVENT_TYPE,
                message=terminal_read.title,
                payload=terminal_audit_payload(terminal_read),
                commit=False,
            )
            db_session.commit()
            db_session.refresh(terminal)
        except Exception:
            db_session.rollback()
            raise

        terminal_read = to_terminal_session_read(terminal)
        return TerminalSessionMutationResult(terminal=terminal_read, changed=changed)

    def list_terminal_jobs(self, *, session_id: str) -> list[TerminalJobRead]:
        terminal_jobs = self._terminal_repository.list_terminal_jobs(session_id=session_id)
        return [to_terminal_job_read(terminal_job) for terminal_job in terminal_jobs]

    def get_terminal_job(self, *, session_id: str, job_id: str) -> TerminalJobRead | None:
        terminal_job = self._terminal_repository.get_terminal_job(
            terminal_job_id=job_id,
            session_id=session_id,
        )
        if terminal_job is None:
            return None
        return to_terminal_job_read(terminal_job)


TerminalSessionService = SessionShellService
