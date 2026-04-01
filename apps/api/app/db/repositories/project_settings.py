from __future__ import annotations

from sqlmodel import Session as DBSession
from sqlmodel import select

from app.db.models import ProjectSettings, utc_now


class ProjectSettingsRepository:
    def __init__(self, db_session: DBSession):
        self.db_session = db_session

    def get_by_project_id(self, project_id: str) -> ProjectSettings | None:
        statement = select(ProjectSettings).where(ProjectSettings.project_id == project_id)
        return self.db_session.exec(statement).first()

    def upsert(
        self,
        *,
        project_id: str,
        default_workflow_template: str | None,
        default_runtime_profile_name: str | None,
        default_queue_backend: str | None,
        runtime_defaults: dict[str, object] | None,
        notes: str | None,
    ) -> ProjectSettings:
        project_settings = self.get_by_project_id(project_id)
        now = utc_now()
        if project_settings is None:
            project_settings = ProjectSettings(
                project_id=project_id,
                default_workflow_template=default_workflow_template,
                default_runtime_profile_name=default_runtime_profile_name,
                default_queue_backend=default_queue_backend,
                runtime_defaults_json=dict(runtime_defaults or {}),
                notes=notes,
                created_at=now,
                updated_at=now,
            )
        else:
            project_settings.default_workflow_template = default_workflow_template
            project_settings.default_runtime_profile_name = default_runtime_profile_name
            project_settings.default_queue_backend = default_queue_backend
            project_settings.runtime_defaults_json = dict(runtime_defaults or {})
            project_settings.notes = notes
            project_settings.updated_at = now

        self.db_session.add(project_settings)
        self.db_session.commit()
        self.db_session.refresh(project_settings)
        return project_settings
