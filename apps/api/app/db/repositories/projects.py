from __future__ import annotations

from sqlmodel import Session as DBSession
from sqlmodel import col, or_, select

from app.db.models import Project, Session, utc_now


class ProjectRepository:
    def __init__(self, db_session: DBSession):
        self.db_session = db_session

    def create_project(self, *, name: str, description: str | None = None) -> Project:
        project = Project(name=name, description=description)
        self.db_session.add(project)
        self.db_session.commit()
        self.db_session.refresh(project)
        return project

    def list_projects(
        self,
        *,
        include_deleted: bool = False,
        query: str | None = None,
        offset: int = 0,
        limit: int = 50,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
    ) -> list[Project]:
        statement = select(Project)
        if not include_deleted:
            statement = statement.where(col(Project.deleted_at).is_(None))

        if query is not None and query.strip():
            like_query = f"%{query.strip()}%"
            statement = statement.where(
                or_(
                    col(Project.name).like(like_query),
                    col(Project.description).like(like_query),
                )
            )

        sort_column = {
            "created_at": col(Project.created_at),
            "name": col(Project.name),
        }.get(sort_by, col(Project.updated_at))
        statement = statement.order_by(
            sort_column.asc() if sort_order == "asc" else sort_column.desc(),
            col(Project.created_at).desc(),
        )
        statement = statement.offset(offset).limit(limit)
        return list(self.db_session.exec(statement).all())

    def count_projects(self, *, include_deleted: bool = False, query: str | None = None) -> int:
        return len(
            self.list_projects(
                include_deleted=include_deleted,
                query=query,
                offset=0,
                limit=1_000_000,
            )
        )

    def get_project(self, project_id: str, *, include_deleted: bool = False) -> Project | None:
        statement = select(Project).where(Project.id == project_id)
        if not include_deleted:
            statement = statement.where(col(Project.deleted_at).is_(None))
        return self.db_session.exec(statement).first()

    def update_project(
        self,
        project: Project,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Project:
        has_changes = False

        if name is not None and name != project.name:
            project.name = name
            has_changes = True

        if description is not None and description != project.description:
            project.description = description
            has_changes = True

        if has_changes:
            project.updated_at = utc_now()
            self.db_session.add(project)
            self.db_session.commit()
            self.db_session.refresh(project)

        return project

    def soft_delete_project(self, project: Project) -> Project:
        deleted_at = utc_now()
        project.deleted_at = deleted_at
        project.updated_at = deleted_at
        self.db_session.add(project)
        self.db_session.commit()
        self.db_session.refresh(project)
        return project

    def restore_project(self, project: Project) -> Project:
        project.deleted_at = None
        project.updated_at = utc_now()
        self.db_session.add(project)
        self.db_session.commit()
        self.db_session.refresh(project)
        return project

    def list_sessions_for_project(self, project_id: str) -> list[Session]:
        statement = (
            select(Session)
            .where(Session.project_id == project_id)
            .where(col(Session.deleted_at).is_(None))
            .order_by(col(Session.updated_at).desc(), col(Session.created_at).desc())
        )
        return list(self.db_session.exec(statement).all())
