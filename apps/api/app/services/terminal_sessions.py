from __future__ import annotations

from dataclasses import dataclass

from app.core.events import SessionEventType
from app.db.models import (
    RuntimeTerminalJobStatus,
    Session,
    TerminalJobRead,
    TerminalJobsCleanupResult,
    TerminalJobTailRead,
    TerminalSessionCreateRequest,
    TerminalSessionRead,
    to_terminal_job_read,
    to_terminal_session_read,
)
from app.db.repositories import RunLogRepository, TerminalRepository

TERMINAL_RUN_LOG_SOURCE = "terminal"
TERMINAL_CREATED_EVENT_TYPE = SessionEventType.TERMINAL_SESSION_CREATED.value
TERMINAL_CLOSED_EVENT_TYPE = SessionEventType.TERMINAL_SESSION_CLOSED.value
TERMINAL_JOB_STARTED_EVENT_TYPE = SessionEventType.TERMINAL_JOB_STARTED.value
TERMINAL_JOB_COMPLETED_EVENT_TYPE = SessionEventType.TERMINAL_JOB_COMPLETED.value
TERMINAL_JOB_FAILED_EVENT_TYPE = SessionEventType.TERMINAL_JOB_FAILED.value
TERMINAL_JOB_CANCELLED_EVENT_TYPE = SessionEventType.TERMINAL_JOB_CANCELLED.value
TERMINAL_JOB_CLEANUP_EVENT_TYPE = "terminal.job.cleanup"


@dataclass(slots=True)
class TerminalSessionMutationResult:
    terminal: TerminalSessionRead
    changed: bool


@dataclass(slots=True)
class TerminalJobMutationResult:
    job: TerminalJobRead
    changed: bool


def terminal_audit_payload(
    terminal: TerminalSessionRead,
    *,
    reason: str | None = None,
    exit_code: int | None = None,
    job_id: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": terminal.id,
        "session_id": terminal.session_id,
        "title": terminal.title,
        "status": terminal.status.value,
        "created_at": terminal.created_at.isoformat(),
        "updated_at": terminal.updated_at.isoformat(),
        "closed_at": terminal.closed_at.isoformat() if terminal.closed_at is not None else None,
    }
    if reason is not None:
        payload["reason"] = reason
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if job_id is not None:
        payload["job_id"] = job_id
    return payload


def terminal_job_audit_payload(
    job: TerminalJobRead,
    *,
    terminal: TerminalSessionRead | None = None,
    reason: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": job.id,
        "terminal_id": job.terminal_session_id,
        "session_id": job.session_id,
        "status": job.status.value,
        "command": job.command,
        "exit_code": job.exit_code,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at is not None else None,
        "ended_at": job.ended_at.isoformat() if job.ended_at is not None else None,
        "metadata": dict(job.metadata_payload),
    }
    if terminal is not None:
        payload["terminal_title"] = terminal.title
    if reason is not None:
        payload["reason"] = reason
    return payload


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
        reason: str | None = None,
        exit_code: int | None = None,
        job_id: str | None = None,
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
                payload=terminal_audit_payload(
                    terminal_read,
                    reason=reason,
                    exit_code=exit_code,
                    job_id=job_id,
                ),
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

    def get_persisted_terminal_job_tail(
        self,
        *,
        session_id: str,
        job_id: str,
        stream: str,
        lines: int,
    ) -> str:
        terminal_job = self._terminal_repository.get_terminal_job(
            terminal_job_id=job_id,
            session_id=session_id,
        )
        if terminal_job is None:
            return ""
        persisted_tail = terminal_job.metadata_json.get(f"{stream}_tail")
        if not isinstance(persisted_tail, str) or not persisted_tail:
            return ""
        persisted_lines = persisted_tail.splitlines()
        return "\n".join(persisted_lines[-lines:])

    def start_terminal_job(
        self,
        *,
        session: Session,
        terminal_id: str,
        command: str,
        metadata: object | None = None,
        detached_conflict_only: bool = False,
    ) -> TerminalJobMutationResult:
        existing_terminal = self._terminal_repository.get_terminal_session(
            session_id=session.id,
            terminal_session_id=terminal_id,
        )
        if existing_terminal is None:
            raise ValueError("Terminal session not found.")

        running_job = (
            self._terminal_repository.get_running_detached_terminal_job(
                session_id=session.id,
                terminal_session_id=terminal_id,
            )
            if detached_conflict_only
            else self._terminal_repository.get_running_terminal_job(
                session_id=session.id,
                terminal_session_id=terminal_id,
            )
        )
        if running_job is not None:
            return TerminalJobMutationResult(job=to_terminal_job_read(running_job), changed=False)

        db_session = self._terminal_repository.db_session
        try:
            terminal_job = self._terminal_repository.create_terminal_job(
                terminal_session_id=terminal_id,
                session_id=session.id,
                command=command,
                status=RuntimeTerminalJobStatus.RUNNING,
                metadata=metadata,
                commit=False,
            )
            terminal_job_read = to_terminal_job_read(terminal_job)
            self._run_log_repository.create_log(
                session_id=session.id,
                project_id=session.project_id,
                run_id=None,
                level="info",
                source=TERMINAL_RUN_LOG_SOURCE,
                event_type=TERMINAL_JOB_STARTED_EVENT_TYPE,
                message=command,
                payload=terminal_job_audit_payload(
                    terminal_job_read,
                    terminal=to_terminal_session_read(existing_terminal),
                ),
                commit=False,
            )
            db_session.commit()
            db_session.refresh(terminal_job)
        except Exception:
            db_session.rollback()
            raise

        return TerminalJobMutationResult(job=to_terminal_job_read(terminal_job), changed=True)

    def finish_terminal_job(
        self,
        *,
        session: Session,
        job_id: str,
        status: RuntimeTerminalJobStatus,
        exit_code: int | None,
        reason: str | None = None,
        metadata_updates: object | None = None,
    ) -> TerminalJobMutationResult | None:
        terminal_job = self._terminal_repository.get_terminal_job(
            terminal_job_id=job_id,
            session_id=session.id,
        )
        if terminal_job is None:
            return None

        if terminal_job.ended_at is not None:
            return TerminalJobMutationResult(job=to_terminal_job_read(terminal_job), changed=False)

        terminal = self._terminal_repository.get_terminal_session(
            session_id=session.id,
            terminal_session_id=terminal_job.terminal_session_id,
        )
        db_session = self._terminal_repository.db_session
        try:
            updated_job = self._terminal_repository.finalize_terminal_job(
                terminal_job,
                status=status,
                exit_code=exit_code,
                metadata_updates=metadata_updates,
                commit=False,
            )
            updated_job_read = to_terminal_job_read(updated_job)
            event_type = {
                RuntimeTerminalJobStatus.COMPLETED: TERMINAL_JOB_COMPLETED_EVENT_TYPE,
                RuntimeTerminalJobStatus.FAILED: TERMINAL_JOB_FAILED_EVENT_TYPE,
                RuntimeTerminalJobStatus.CANCELLED: TERMINAL_JOB_CANCELLED_EVENT_TYPE,
            }[status]
            self._run_log_repository.create_log(
                session_id=session.id,
                project_id=session.project_id,
                run_id=None,
                level="info",
                source=TERMINAL_RUN_LOG_SOURCE,
                event_type=event_type,
                message=updated_job.command,
                payload=terminal_job_audit_payload(
                    updated_job_read,
                    terminal=(to_terminal_session_read(terminal) if terminal is not None else None),
                    reason=reason,
                ),
                commit=False,
            )
            db_session.commit()
            db_session.refresh(updated_job)
        except Exception:
            db_session.rollback()
            raise

        return TerminalJobMutationResult(job=to_terminal_job_read(updated_job), changed=True)

    def build_terminal_job_tail(
        self,
        *,
        job: TerminalJobRead,
        stream: str,
        lines: int,
        content: str,
    ) -> TerminalJobTailRead:
        normalized_stream = "stderr" if stream == "stderr" else "stdout"
        return TerminalJobTailRead(
            job_id=job.id,
            session_id=job.session_id,
            terminal_session_id=job.terminal_session_id,
            status=job.status,
            stream=normalized_stream,
            lines=lines,
            tail=content,
            ended_at=job.ended_at,
            updated_at=job.updated_at,
        )

    def cleanup_finished_jobs(
        self,
        *,
        session: Session,
        active_job_ids: set[str],
        limit: int | None = None,
    ) -> TerminalJobsCleanupResult:
        finished_jobs = self._terminal_repository.list_finished_terminal_jobs(session_id=session.id)
        deletable_ids: list[str] = [job.id for job in finished_jobs if job.id not in active_job_ids]
        if limit is not None:
            deletable_ids = deletable_ids[:limit]

        db_session = self._terminal_repository.db_session
        try:
            deleted = self._terminal_repository.delete_terminal_jobs(
                session_id=session.id,
                job_ids=set(deletable_ids),
                commit=False,
            )
            kept = len(finished_jobs) - deleted
            self._run_log_repository.create_log(
                session_id=session.id,
                project_id=session.project_id,
                run_id=None,
                level="info",
                source=TERMINAL_RUN_LOG_SOURCE,
                event_type=TERMINAL_JOB_CLEANUP_EVENT_TYPE,
                message="Cleaned finished terminal jobs.",
                payload={
                    "deleted_jobs": deleted,
                    "kept_jobs": kept,
                    "active_job_ids": sorted(active_job_ids),
                    "limit": limit,
                },
                commit=False,
            )
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise
        return TerminalJobsCleanupResult(deleted_jobs=deleted, kept_jobs=kept)


TerminalSessionService = SessionShellService
