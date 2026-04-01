from __future__ import annotations

from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.db.models import SkillRecord, SkillRecordStatus


class SkillRepository:
    def __init__(self, db_session: DBSession):
        self.db_session = db_session

    def list_skills(self) -> list[SkillRecord]:
        statement = select(SkillRecord).order_by(
            col(SkillRecord.source).asc(),
            col(SkillRecord.scope).asc(),
            col(SkillRecord.name).asc(),
            col(SkillRecord.entry_file).asc(),
        )
        return list(self.db_session.exec(statement).all())

    def get_skill(self, skill_id: str) -> SkillRecord | None:
        return self.db_session.get(SkillRecord, skill_id)

    def replace_all(self, records: list[SkillRecord]) -> None:
        existing_by_entry = {record.entry_file: record for record in self.list_skills()}
        seen_entries = {record.entry_file for record in records}

        for record in records:
            existing = existing_by_entry.get(record.entry_file)
            if existing is None:
                self.db_session.add(record)
                continue

            existing.source = record.source
            existing.scope = record.scope
            existing.root_dir = record.root_dir
            existing.directory_name = record.directory_name
            existing.name = record.name
            existing.description = record.description
            existing.compatibility_json = list(record.compatibility_json)
            existing.metadata_json = dict(record.metadata_json)
            existing.parameter_schema_json = dict(record.parameter_schema_json)
            existing.raw_frontmatter_json = dict(record.raw_frontmatter_json)
            existing.status = record.status
            existing.error_message = record.error_message
            existing.content_hash = record.content_hash
            existing.last_scanned_at = record.last_scanned_at
            self.db_session.add(existing)

        for existing in existing_by_entry.values():
            if (
                existing.entry_file not in seen_entries
                and existing.status != SkillRecordStatus.IGNORED
            ):
                existing.status = SkillRecordStatus.IGNORED
                existing.error_message = "Skill entry was not found in latest scan."
                self.db_session.add(existing)

        self.db_session.commit()

    def set_enabled(self, skill: SkillRecord, enabled: bool) -> SkillRecord:
        if skill.enabled != enabled:
            skill.enabled = enabled
            self.db_session.add(skill)
            self.db_session.commit()
            self.db_session.refresh(skill)
        return skill
