from __future__ import annotations

from sqlmodel import Session as DBSession
from sqlmodel import col, or_, select

from app.db.models import RunLog


class RunLogRepository:
    def __init__(self, db_session: DBSession):
        self.db_session = db_session

    def create_log(
        self,
        *,
        session_id: str | None,
        level: str,
        source: str,
        event_type: str,
        message: str,
        payload: dict[str, object],
        project_id: str | None = None,
        run_id: str | None = None,
        commit: bool = True,
    ) -> RunLog:
        run_log = RunLog(
            session_id=session_id,
            project_id=project_id,
            run_id=run_id,
            level=level,
            source=source,
            event_type=event_type,
            message=message,
            payload_json=payload,
        )
        self.db_session.add(run_log)
        if commit:
            self.db_session.commit()
            self.db_session.refresh(run_log)
        else:
            self.db_session.flush()
        return run_log

    def list_logs(
        self,
        *,
        session_id: str | None = None,
        project_id: str | None = None,
        level: str | None = None,
        source: str | None = None,
        event_type: str | None = None,
        query: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        offset: int = 0,
        limit: int = 20,
    ) -> list[RunLog]:
        statement = select(RunLog)
        if session_id is not None:
            statement = statement.where(RunLog.session_id == session_id)
        if project_id is not None:
            statement = statement.where(RunLog.project_id == project_id)
        if level is not None:
            statement = statement.where(RunLog.level == level)
        if source is not None:
            statement = statement.where(RunLog.source == source)
        if event_type is not None:
            statement = statement.where(RunLog.event_type == event_type)
        if query is not None and query.strip():
            like_query = f"%{query.strip()}%"
            statement = statement.where(
                or_(
                    col(RunLog.message).like(like_query),
                    col(RunLog.event_type).like(like_query),
                    col(RunLog.source).like(like_query),
                )
            )

        if sort_by == "created_at":
            statement = statement.order_by(
                (
                    col(RunLog.created_at).asc()
                    if sort_order == "asc"
                    else col(RunLog.created_at).desc()
                ),
                col(RunLog.id).desc(),
            )
        else:
            statement = statement.order_by(
                col(RunLog.id).asc() if sort_order == "asc" else col(RunLog.id).desc()
            )
        statement = statement.offset(offset).limit(limit)
        return list(self.db_session.exec(statement).all())

    def count_logs(
        self,
        *,
        session_id: str | None = None,
        project_id: str | None = None,
        level: str | None = None,
        source: str | None = None,
        event_type: str | None = None,
        query: str | None = None,
    ) -> int:
        return len(
            self.list_logs(
                session_id=session_id,
                project_id=project_id,
                level=level,
                source=source,
                event_type=event_type,
                query=query,
                offset=0,
                limit=1_000_000,
            )
        )

    def get_latest_log(
        self,
        *,
        source: str,
        event_type: str,
        session_id: str | None = None,
    ) -> RunLog | None:
        statement = select(RunLog).where(RunLog.source == source, RunLog.event_type == event_type)
        if session_id is not None:
            statement = statement.where(RunLog.session_id == session_id)
        statement = statement.order_by(col(RunLog.created_at).desc(), col(RunLog.id).desc()).limit(
            1
        )
        return self.db_session.exec(statement).first()
