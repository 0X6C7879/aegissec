from __future__ import annotations

from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.db.models import Message, MessageRole, Session, SessionStatus, utc_now


class SessionRepository:
    def __init__(self, db_session: DBSession):
        self.db_session = db_session

    def create_session(self, title: str | None = None) -> Session:
        session = Session(title=title or "New Session")
        self.db_session.add(session)
        self.db_session.commit()
        self.db_session.refresh(session)
        return session

    def list_sessions(self, *, include_deleted: bool = False) -> list[Session]:
        statement = select(Session)
        if not include_deleted:
            statement = statement.where(col(Session.deleted_at).is_(None))

        statement = statement.order_by(
            col(Session.updated_at).desc(), col(Session.created_at).desc()
        )
        return list(self.db_session.exec(statement).all())

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
    ) -> Session:
        has_changes = False

        if title is not None and title != session.title:
            session.title = title
            has_changes = True

        if status is not None and status != session.status:
            session.status = status
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

    def list_messages(self, session_id: str) -> list[Message]:
        statement = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(col(Message.created_at).asc(), col(Message.id).asc())
        )
        return list(self.db_session.exec(statement).all())
