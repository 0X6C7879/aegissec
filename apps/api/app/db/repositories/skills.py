from __future__ import annotations

from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.db.models import SkillRecord


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
        for record in self.list_skills():
            self.db_session.delete(record)

        for record in records:
            self.db_session.add(record)

        self.db_session.commit()
