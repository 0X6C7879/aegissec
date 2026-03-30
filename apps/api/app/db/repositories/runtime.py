from __future__ import annotations

from datetime import datetime

from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.db.models import ExecutionStatus, RuntimeArtifact, RuntimeExecutionRun, utc_now


class RuntimeRepository:
    def __init__(self, db_session: DBSession):
        self.db_session = db_session

    def create_run(
        self,
        *,
        session_id: str | None,
        command: str,
        requested_timeout_seconds: int,
        status: ExecutionStatus,
        exit_code: int | None,
        stdout: str,
        stderr: str,
        container_name: str,
        started_at: datetime,
        ended_at: datetime,
        artifacts: list[tuple[str, str, str]],
    ) -> tuple[RuntimeExecutionRun, list[RuntimeArtifact]]:
        created_at = utc_now()
        run = RuntimeExecutionRun(
            session_id=session_id,
            command=command,
            requested_timeout_seconds=requested_timeout_seconds,
            status=status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            container_name=container_name,
            created_at=created_at,
            started_at=started_at,
            ended_at=ended_at,
        )
        self.db_session.add(run)
        self.db_session.flush()

        artifact_rows = [
            RuntimeArtifact(
                run_id=run.id,
                relative_path=relative_path,
                host_path=host_path,
                container_path=container_path,
                created_at=created_at,
            )
            for relative_path, host_path, container_path in artifacts
        ]
        for artifact in artifact_rows:
            self.db_session.add(artifact)

        self.db_session.commit()
        self.db_session.refresh(run)
        for artifact in artifact_rows:
            self.db_session.refresh(artifact)

        return run, artifact_rows

    def list_recent_runs(self, *, limit: int) -> list[RuntimeExecutionRun]:
        statement = (
            select(RuntimeExecutionRun)
            .order_by(
                col(RuntimeExecutionRun.started_at).desc(), col(RuntimeExecutionRun.id).desc()
            )
            .limit(limit)
        )
        return list(self.db_session.exec(statement).all())

    def list_recent_artifacts(self, *, limit: int) -> list[RuntimeArtifact]:
        statement = (
            select(RuntimeArtifact)
            .order_by(col(RuntimeArtifact.created_at).desc(), col(RuntimeArtifact.id).desc())
            .limit(limit)
        )
        return list(self.db_session.exec(statement).all())

    def list_artifacts_for_run(self, run_id: str) -> list[RuntimeArtifact]:
        statement = (
            select(RuntimeArtifact)
            .where(RuntimeArtifact.run_id == run_id)
            .order_by(col(RuntimeArtifact.created_at).asc(), col(RuntimeArtifact.id).asc())
        )
        return list(self.db_session.exec(statement).all())
