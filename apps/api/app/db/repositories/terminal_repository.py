from __future__ import annotations

from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.db.models import (
    RuntimeTerminalJob,
    RuntimeTerminalJobStatus,
    RuntimeTerminalSession,
    RuntimeTerminalSessionStatus,
    utc_now,
)


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
        self.db_session.flush()
        if commit:
            self.db_session.commit()
            self.db_session.refresh(terminal_session)
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
        self.db_session.flush()
        if commit:
            self.db_session.commit()
            self.db_session.refresh(terminal_session)
        return terminal_session

    def create_terminal_job(
        self,
        *,
        terminal_session_id: str,
        session_id: str,
        command: str,
        status: RuntimeTerminalJobStatus = RuntimeTerminalJobStatus.QUEUED,
        metadata: dict[str, object] | None = None,
        commit: bool = True,
    ) -> RuntimeTerminalJob:
        terminal_session = self.db_session.get(RuntimeTerminalSession, terminal_session_id)
        if terminal_session is None or terminal_session.session_id != session_id:
            raise ValueError("Terminal session does not belong to the provided session.")
        if terminal_session.status != RuntimeTerminalSessionStatus.OPEN:
            raise ValueError("Terminal session is closed and cannot accept new jobs.")

        terminal_job = RuntimeTerminalJob(
            terminal_session_id=terminal_session_id,
            session_id=session_id,
            command=command,
            status=status,
            metadata_json=dict(metadata or {}),
        )
        self.db_session.add(terminal_job)
        self.db_session.flush()
        if commit:
            self.db_session.commit()
            self.db_session.refresh(terminal_job)
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
