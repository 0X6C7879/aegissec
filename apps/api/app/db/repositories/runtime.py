from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlmodel import Session as DBSession
from sqlmodel import col, or_, select

from app.db.models import (
    ExecutionStatus,
    RuntimeArtifact,
    RuntimeExecutionRun,
    Session,
    SessionStatus,
    utc_now,
)


class RuntimeRepository:
    def __init__(self, db_session: DBSession):
        self.db_session = db_session

    @staticmethod
    def _apply_runtime_run_filters(
        statement: Any,
        *,
        session_id: str | None,
        query: str | None,
    ) -> Any:
        if session_id is not None:
            statement = statement.where(RuntimeExecutionRun.session_id == session_id)
        if query is not None and query.strip():
            statement = statement.where(col(RuntimeExecutionRun.command).like(f"%{query.strip()}%"))
        return statement

    @staticmethod
    def _apply_runtime_artifact_filters(
        statement: Any,
        *,
        session_id: str | None,
        query: str | None,
    ) -> Any:
        if session_id is not None:
            statement = statement.where(
                col(RuntimeArtifact.run_id).in_(
                    select(RuntimeExecutionRun.id).where(
                        RuntimeExecutionRun.session_id == session_id
                    )
                )
            )
        if query is not None and query.strip():
            like_query = f"%{query.strip()}%"
            statement = statement.where(
                or_(
                    col(RuntimeArtifact.relative_path).like(like_query),
                    col(RuntimeArtifact.host_path).like(like_query),
                    col(RuntimeArtifact.container_path).like(like_query),
                )
            )
        return statement

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

    def list_artifacts_for_runs(self, run_ids: Iterable[str]) -> dict[str, list[RuntimeArtifact]]:
        run_id_values = list(dict.fromkeys(run_ids))
        if not run_id_values:
            return {}
        statement = (
            select(RuntimeArtifact)
            .where(col(RuntimeArtifact.run_id).in_(run_id_values))
            .order_by(
                col(RuntimeArtifact.run_id).asc(),
                col(RuntimeArtifact.created_at).asc(),
                col(RuntimeArtifact.id).asc(),
            )
        )
        artifacts_by_run_id: dict[str, list[RuntimeArtifact]] = {}
        for artifact in self.db_session.exec(statement).all():
            artifacts_by_run_id.setdefault(artifact.run_id, []).append(artifact)
        return artifacts_by_run_id

    def list_runs(
        self,
        *,
        session_id: str | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 20,
        sort_by: str = "started_at",
        sort_order: str = "desc",
    ) -> list[RuntimeExecutionRun]:
        statement = select(RuntimeExecutionRun)
        statement = self._apply_runtime_run_filters(
            statement,
            session_id=session_id,
            query=query,
        )

        order_column = (
            col(RuntimeExecutionRun.created_at)
            if sort_by == "created_at"
            else col(RuntimeExecutionRun.started_at)
        )
        statement = statement.order_by(
            order_column.asc() if sort_order == "asc" else order_column.desc(),
            col(RuntimeExecutionRun.id).desc(),
        )
        statement = statement.offset(offset).limit(limit)
        return list(self.db_session.exec(statement).all())

    def count_runs(self, *, session_id: str | None = None, query: str | None = None) -> int:
        statement = select(func.count()).select_from(RuntimeExecutionRun)
        statement = self._apply_runtime_run_filters(
            statement,
            session_id=session_id,
            query=query,
        )
        return int(self.db_session.exec(statement).one())

    def list_artifacts(
        self,
        *,
        session_id: str | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 20,
        sort_by: str = "created_at",
        sort_order: str = "desc",
    ) -> list[RuntimeArtifact]:
        statement = select(RuntimeArtifact)
        statement = self._apply_runtime_artifact_filters(
            statement,
            session_id=session_id,
            query=query,
        )

        if sort_by == "relative_path":
            statement = statement.order_by(
                (
                    col(RuntimeArtifact.relative_path).asc()
                    if sort_order == "asc"
                    else col(RuntimeArtifact.relative_path).desc()
                ),
                col(RuntimeArtifact.id).desc(),
            )
        else:
            statement = statement.order_by(
                (
                    col(RuntimeArtifact.created_at).asc()
                    if sort_order == "asc"
                    else col(RuntimeArtifact.created_at).desc()
                ),
                col(RuntimeArtifact.id).desc(),
            )
        statement = statement.offset(offset).limit(limit)
        return list(self.db_session.exec(statement).all())

    def count_artifacts(self, *, session_id: str | None = None, query: str | None = None) -> int:
        statement = select(func.count()).select_from(RuntimeArtifact)
        statement = self._apply_runtime_artifact_filters(
            statement,
            session_id=session_id,
            query=query,
        )
        return int(self.db_session.exec(statement).one())

    def list_artifacts_ordered_newest(self) -> list[RuntimeArtifact]:
        statement = select(RuntimeArtifact).order_by(
            col(RuntimeArtifact.created_at).desc(), col(RuntimeArtifact.id).desc()
        )
        return list(self.db_session.exec(statement).all())

    def get_artifact(self, artifact_id: str) -> RuntimeArtifact | None:
        return self.db_session.get(RuntimeArtifact, artifact_id)

    def get_runs_by_ids(self, run_ids: set[str]) -> dict[str, RuntimeExecutionRun]:
        if not run_ids:
            return {}
        statement = select(RuntimeExecutionRun).where(col(RuntimeExecutionRun.id).in_(run_ids))
        return {run.id: run for run in self.db_session.exec(statement).all()}

    def get_session_statuses(self, session_ids: set[str]) -> dict[str, SessionStatus]:
        if not session_ids:
            return {}
        statement = select(Session).where(col(Session.id).in_(session_ids))
        return {session.id: session.status for session in self.db_session.exec(statement).all()}

    def delete_artifacts(self, artifacts: list[RuntimeArtifact]) -> int:
        for artifact in artifacts:
            self.db_session.delete(artifact)
        self.db_session.commit()
        return len(artifacts)

    def delete_runs(self, run_ids: set[str]) -> int:
        if not run_ids:
            return 0

        statement = select(RuntimeExecutionRun).where(col(RuntimeExecutionRun.id).in_(run_ids))
        runs = list(self.db_session.exec(statement).all())
        for run in runs:
            self.db_session.delete(run)
        self.db_session.commit()
        return len(runs)
