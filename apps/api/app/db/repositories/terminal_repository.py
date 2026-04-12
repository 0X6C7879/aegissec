from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session as DBSession
from sqlmodel import col, delete, select

from app.db.models import (
    TERMINAL_METADATA_MAX_BYTES,
    RuntimeTerminalJob,
    RuntimeTerminalJobStatus,
    RuntimeTerminalSession,
    RuntimeTerminalSessionStatus,
    _normalize_terminal_metadata_value,
    utc_now,
)


def _flush_pending(db_session: DBSession) -> None:
    try:
        db_session.flush()
    except SQLAlchemyError:
        db_session.rollback()
        raise


def _commit_and_refresh(db_session: DBSession, instance: object) -> None:
    try:
        db_session.commit()
    except SQLAlchemyError:
        db_session.rollback()
        raise

    try:
        db_session.refresh(instance)
    except SQLAlchemyError:
        # The write is already durable once commit() succeeds. Refresh is a
        # best-effort state sync only, so clean up the session but do not
        # surface the operation as a failed write.
        db_session.rollback()


def _normalize_terminal_job_metadata(metadata: object | None) -> dict[str, object]:
    try:
        normalized = _normalize_terminal_metadata_value({} if metadata is None else metadata)
    except ValueError as exc:
        raise ValueError(f"Terminal job metadata is invalid: {exc}") from exc
    if not isinstance(normalized, dict):
        raise ValueError("Terminal job metadata must be an object.")

    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > TERMINAL_METADATA_MAX_BYTES:
        raise ValueError(
            f"Terminal job metadata exceeds max size of {TERMINAL_METADATA_MAX_BYTES} bytes."
        )

    return normalized


def _get_terminal_job_initial_state(
    status: RuntimeTerminalJobStatus,
) -> tuple[datetime | None, datetime | None, int | None]:
    if status == RuntimeTerminalJobStatus.QUEUED:
        return None, None, None
    if status == RuntimeTerminalJobStatus.RUNNING:
        return utc_now(), None, None
    raise ValueError("Terminal jobs can only be created in queued or running state.")


class TerminalRepository:
    def __init__(self, db_session: DBSession):
        self.db_session = db_session

    def create_terminal_session(
        self,
        *,
        session_id: str,
        title: str,
        shell: str,
        cwd: str,
        metadata: dict[str, object],
        commit: bool = True,
    ) -> RuntimeTerminalSession:
        terminal_session = RuntimeTerminalSession(
            session_id=session_id,
            title=title,
            shell=shell,
            cwd=cwd,
            metadata_json=dict(metadata),
        )
        self.db_session.add(terminal_session)
        _flush_pending(self.db_session)
        if commit:
            _commit_and_refresh(self.db_session, terminal_session)
        return terminal_session

    def list_terminal_sessions(
        self,
        *,
        session_id: str,
        include_closed: bool = True,
    ) -> list[RuntimeTerminalSession]:
        statement = select(RuntimeTerminalSession).where(
            RuntimeTerminalSession.session_id == session_id
        )
        if not include_closed:
            statement = statement.where(
                RuntimeTerminalSession.status == RuntimeTerminalSessionStatus.OPEN
            )
        statement = statement.order_by(
            col(RuntimeTerminalSession.created_at).desc(),
            col(RuntimeTerminalSession.id).desc(),
        )
        return list(self.db_session.exec(statement).all())

    def get_terminal_session(
        self,
        *,
        session_id: str,
        terminal_session_id: str,
    ) -> RuntimeTerminalSession | None:
        statement = select(RuntimeTerminalSession).where(
            RuntimeTerminalSession.id == terminal_session_id,
            RuntimeTerminalSession.session_id == session_id,
        )
        return self.db_session.exec(statement).first()

    def close_terminal_session(
        self,
        terminal_session: RuntimeTerminalSession,
        *,
        commit: bool = True,
    ) -> RuntimeTerminalSession:
        if terminal_session.status == RuntimeTerminalSessionStatus.CLOSED:
            return terminal_session

        closed_at = utc_now()
        terminal_session.status = RuntimeTerminalSessionStatus.CLOSED
        terminal_session.closed_at = closed_at
        terminal_session.updated_at = closed_at
        self.db_session.add(terminal_session)
        _flush_pending(self.db_session)
        if commit:
            _commit_and_refresh(self.db_session, terminal_session)
        return terminal_session

    def create_terminal_job(
        self,
        *,
        terminal_session_id: str,
        session_id: str,
        command: str,
        status: RuntimeTerminalJobStatus = RuntimeTerminalJobStatus.QUEUED,
        metadata: object | None = None,
        commit: bool = True,
    ) -> RuntimeTerminalJob:
        terminal_session = self.db_session.get(RuntimeTerminalSession, terminal_session_id)
        if terminal_session is None or terminal_session.session_id != session_id:
            raise ValueError("Terminal session does not belong to the provided session.")
        if terminal_session.status != RuntimeTerminalSessionStatus.OPEN:
            raise ValueError("Terminal session is closed and cannot accept new jobs.")

        metadata_json = _normalize_terminal_job_metadata(metadata)
        started_at, ended_at, exit_code = _get_terminal_job_initial_state(status)

        terminal_job = RuntimeTerminalJob(
            terminal_session_id=terminal_session_id,
            session_id=session_id,
            command=command,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            exit_code=exit_code,
            metadata_json=metadata_json,
        )
        self.db_session.add(terminal_job)
        _flush_pending(self.db_session)
        if commit:
            _commit_and_refresh(self.db_session, terminal_job)
        return terminal_job

    def list_terminal_jobs(
        self,
        *,
        session_id: str,
        terminal_session_id: str | None = None,
    ) -> list[RuntimeTerminalJob]:
        statement = select(RuntimeTerminalJob).where(RuntimeTerminalJob.session_id == session_id)
        if terminal_session_id is not None:
            statement = statement.where(
                RuntimeTerminalJob.terminal_session_id == terminal_session_id
            )
        statement = statement.order_by(
            col(RuntimeTerminalJob.created_at).desc(),
            col(RuntimeTerminalJob.id).desc(),
        )
        return list(self.db_session.exec(statement).all())

    def get_terminal_job(
        self,
        *,
        terminal_job_id: str,
        session_id: str,
    ) -> RuntimeTerminalJob | None:
        statement = select(RuntimeTerminalJob).where(
            RuntimeTerminalJob.id == terminal_job_id,
            RuntimeTerminalJob.session_id == session_id,
        )
        return self.db_session.exec(statement).first()

    def get_running_terminal_job(
        self,
        *,
        session_id: str,
        terminal_session_id: str,
    ) -> RuntimeTerminalJob | None:
        statement = (
            select(RuntimeTerminalJob)
            .where(
                RuntimeTerminalJob.session_id == session_id,
                RuntimeTerminalJob.terminal_session_id == terminal_session_id,
                RuntimeTerminalJob.status == RuntimeTerminalJobStatus.RUNNING,
            )
            .order_by(
                col(RuntimeTerminalJob.created_at).desc(),
                col(RuntimeTerminalJob.id).desc(),
            )
        )
        return self.db_session.exec(statement).first()

    def get_running_detached_terminal_job(
        self,
        *,
        session_id: str,
        terminal_session_id: str,
    ) -> RuntimeTerminalJob | None:
        statement = (
            select(RuntimeTerminalJob)
            .where(
                RuntimeTerminalJob.session_id == session_id,
                RuntimeTerminalJob.terminal_session_id == terminal_session_id,
                RuntimeTerminalJob.status == RuntimeTerminalJobStatus.RUNNING,
            )
            .order_by(
                col(RuntimeTerminalJob.created_at).desc(),
                col(RuntimeTerminalJob.id).desc(),
            )
        )
        running_jobs = self.db_session.exec(statement).all()
        for job in running_jobs:
            if job.metadata_json.get("detach") is True:
                return job
        return None

    def finalize_terminal_job(
        self,
        terminal_job: RuntimeTerminalJob,
        *,
        status: RuntimeTerminalJobStatus,
        exit_code: int | None,
        metadata_updates: object | None = None,
        commit: bool = True,
    ) -> RuntimeTerminalJob:
        if status in {RuntimeTerminalJobStatus.QUEUED, RuntimeTerminalJobStatus.RUNNING}:
            raise ValueError("Finalized terminal jobs must end in a terminal state.")
        if terminal_job.ended_at is not None:
            return terminal_job

        metadata_json = dict(terminal_job.metadata_json)
        if metadata_updates is not None:
            metadata_json.update(_normalize_terminal_job_metadata(metadata_updates))

        now = utc_now()
        if terminal_job.started_at is None:
            terminal_job.started_at = now
        terminal_job.status = status
        terminal_job.exit_code = exit_code
        terminal_job.ended_at = now
        terminal_job.updated_at = now
        terminal_job.metadata_json = metadata_json
        self.db_session.add(terminal_job)
        _flush_pending(self.db_session)
        if commit:
            _commit_and_refresh(self.db_session, terminal_job)
        return terminal_job

    def list_finished_terminal_jobs(
        self,
        *,
        session_id: str,
    ) -> list[RuntimeTerminalJob]:
        statement = (
            select(RuntimeTerminalJob)
            .where(
                RuntimeTerminalJob.session_id == session_id,
                col(RuntimeTerminalJob.ended_at).is_not(None),
            )
            .order_by(
                col(RuntimeTerminalJob.updated_at).desc(),
                col(RuntimeTerminalJob.id).desc(),
            )
        )
        return list(self.db_session.exec(statement).all())

    def delete_terminal_jobs(
        self,
        *,
        session_id: str,
        job_ids: set[str],
        commit: bool = True,
    ) -> int:
        if not job_ids:
            return 0
        statement = delete(RuntimeTerminalJob).where(
            col(RuntimeTerminalJob.session_id) == session_id
        )
        statement = statement.where(col(RuntimeTerminalJob.id).in_(job_ids))
        result = self.db_session.exec(statement)
        if commit:
            self.db_session.commit()
        return int(result.rowcount or 0)
