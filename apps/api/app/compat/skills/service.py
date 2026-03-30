from __future__ import annotations

from fastapi import Depends
from sqlmodel import Session as DBSession

from app.core.settings import Settings, get_settings
from app.db.models import SkillRecord, SkillRecordRead, to_skill_record_read
from app.db.repositories import SkillRepository
from app.db.session import get_db_session

from .models import ParsedSkillRecordData, SkillScanRoot
from .parser import parse_skill_file
from .scanner import default_skill_scan_roots, scan_skill_files


class SkillService:
    def __init__(self, db_session: DBSession, settings: Settings) -> None:
        self._repository = SkillRepository(db_session)
        self._settings = settings

    def list_skills(self) -> list[SkillRecordRead]:
        return [to_skill_record_read(record) for record in self._repository.list_skills()]

    def get_skill(self, skill_id: str) -> SkillRecordRead | None:
        record = self._repository.get_skill(skill_id)
        if record is None:
            return None
        return to_skill_record_read(record)

    def rescan_skills(self) -> list[SkillRecordRead]:
        records = [self._to_skill_record(parsed) for parsed in self._scan_and_parse()]
        self._repository.replace_all(records)
        return self.list_skills()

    def _scan_and_parse(self) -> list[ParsedSkillRecordData]:
        discovered_files = scan_skill_files(resolve_skill_scan_roots(self._settings))
        return [parse_skill_file(discovered_file) for discovered_file in discovered_files]

    @staticmethod
    def _to_skill_record(parsed: ParsedSkillRecordData) -> SkillRecord:
        return SkillRecord(
            id=parsed.id,
            source=parsed.source,
            scope=parsed.scope,
            root_dir=parsed.root_dir,
            directory_name=parsed.directory_name,
            entry_file=parsed.entry_file,
            name=parsed.name,
            description=parsed.description,
            compatibility_json=parsed.compatibility,
            metadata_json=parsed.metadata,
            raw_frontmatter_json=parsed.raw_frontmatter,
            status=parsed.status,
            error_message=parsed.error_message,
            content_hash=parsed.content_hash,
            last_scanned_at=parsed.last_scanned_at,
        )


def resolve_skill_scan_roots(settings: Settings) -> list[SkillScanRoot]:
    _ = settings
    return default_skill_scan_roots()


def get_skill_service(
    db_session: DBSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> SkillService:
    return SkillService(db_session, settings)
