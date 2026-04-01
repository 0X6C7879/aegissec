from __future__ import annotations

from sqlmodel import Session as DBSession
from sqlmodel import col, or_, select

from app.db.models import Message, MessageRole, Session, SessionStatus, utc_now


class SessionRepository:
    def __init__(self, db_session: DBSession):
        self.db_session = db_session

    def create_session(
        self,
        title: str | None = None,
        *,
        project_id: str | None = None,
        goal: str | None = None,
        scenario_type: str | None = None,
        current_phase: str | None = None,
        runtime_policy_json: dict[str, object] | None = None,
        runtime_profile_name: str | None = None,
    ) -> Session:
        session = Session(
            title=title or "New Session",
            project_id=project_id,
            goal=goal,
            scenario_type=scenario_type,
            current_phase=current_phase,
            runtime_policy_json=runtime_policy_json,
            runtime_profile_name=runtime_profile_name,
        )
        self.db_session.add(session)
        self.db_session.commit()
        self.db_session.refresh(session)
        return session

    def list_sessions(
        self,
        *,
        include_deleted: bool = False,
        project_id: str | None = None,
        status: SessionStatus | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 50,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
    ) -> list[Session]:
        statement = select(Session)
        if not include_deleted:
            statement = statement.where(col(Session.deleted_at).is_(None))

        if project_id is not None:
            statement = statement.where(Session.project_id == project_id)

        if status is not None:
            statement = statement.where(Session.status == status)

        if query is not None and query.strip():
            like_query = f"%{query.strip()}%"
            statement = statement.where(
                or_(
                    col(Session.title).like(like_query),
                    col(Session.goal).like(like_query),
                    col(Session.scenario_type).like(like_query),
                    col(Session.current_phase).like(like_query),
                )
            )

        sort_column = {
            "created_at": col(Session.created_at),
            "title": col(Session.title),
            "status": col(Session.status),
        }.get(sort_by, col(Session.updated_at))
        statement = statement.order_by(
            sort_column.asc() if sort_order == "asc" else sort_column.desc(),
            col(Session.created_at).desc(),
        )
        statement = statement.offset(offset).limit(limit)
        return list(self.db_session.exec(statement).all())

    def count_sessions(
        self,
        *,
        include_deleted: bool = False,
        project_id: str | None = None,
        status: SessionStatus | None = None,
        query: str | None = None,
    ) -> int:
        return len(
            self.list_sessions(
                include_deleted=include_deleted,
                project_id=project_id,
                status=status,
                query=query,
                offset=0,
                limit=1_000_000,
            )
        )

    def get_session(self, session_id: str, *, include_deleted: bool = False) -> Session | None:
        statement = select(Session).where(Session.id == session_id)
        if not include_deleted:
            statement = statement.where(col(Session.deleted_at).is_(None))

        return self.db_session.exec(statement).first()

    def update_session(
        self,
        session: Session,
        *,
        title: str | None = None,
        status: SessionStatus | None = None,
        project_id: str | None = None,
        goal: str | None = None,
        scenario_type: str | None = None,
        current_phase: str | None = None,
        runtime_policy_json: dict[str, object] | None = None,
        runtime_profile_name: str | None = None,
    ) -> Session:
        has_changes = False

        if title is not None and title != session.title:
            session.title = title
            has_changes = True

        if status is not None and status != session.status:
            session.status = status
            has_changes = True

        if project_id is not None and project_id != session.project_id:
            session.project_id = project_id
            has_changes = True

        if goal is not None and goal != session.goal:
            session.goal = goal
            has_changes = True

        if scenario_type is not None and scenario_type != session.scenario_type:
            session.scenario_type = scenario_type
            has_changes = True

        if current_phase is not None and current_phase != session.current_phase:
            session.current_phase = current_phase
            has_changes = True

        if runtime_policy_json is not None and runtime_policy_json != session.runtime_policy_json:
            session.runtime_policy_json = runtime_policy_json
            has_changes = True

        if (
            runtime_profile_name is not None
            and runtime_profile_name != session.runtime_profile_name
        ):
            session.runtime_profile_name = runtime_profile_name
            has_changes = True

        if has_changes:
            session.updated_at = utc_now()
            self.db_session.add(session)
            self.db_session.commit()
            self.db_session.refresh(session)

        return session

    def soft_delete_session(self, session: Session) -> Session:
        deleted_at = utc_now()
        session.deleted_at = deleted_at
        session.updated_at = deleted_at
        self.db_session.add(session)
        self.db_session.commit()
        self.db_session.refresh(session)
        return session

    def restore_session(self, session: Session) -> Session:
        session.deleted_at = None
        session.updated_at = utc_now()
        self.db_session.add(session)
        self.db_session.commit()
        self.db_session.refresh(session)
        return session

    def create_message(
        self,
        *,
        session: Session,
        role: MessageRole,
        content: str,
        attachments: list[dict[str, str | int | None]],
    ) -> Message:
        message = Message(
            session_id=session.id,
            role=role,
            content=content,
            attachments_json=attachments,
        )
        session.updated_at = utc_now()
        self.db_session.add(message)
        self.db_session.add(session)
        self.db_session.commit()
        self.db_session.refresh(message)
        self.db_session.refresh(session)
        return message

    def get_message(self, message_id: str) -> Message | None:
        statement = select(Message).where(Message.id == message_id)
        return self.db_session.exec(statement).first()

    def update_message_content(self, message: Message, content: str) -> Message:
        if content == message.content:
            return message

        message.content = content
        self.db_session.add(message)
        self.db_session.commit()
        self.db_session.refresh(message)
        return message

    def list_messages(self, session_id: str) -> list[Message]:
        statement = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(col(Message.created_at).asc(), col(Message.id).asc())
        )
        return list(self.db_session.exec(statement).all())
