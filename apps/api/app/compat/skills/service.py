from __future__ import annotations

from pathlib import Path

from fastapi import Depends
from sqlmodel import Session as DBSession

from app.core.settings import Settings, get_settings
from app.db.models import (
    SkillAgentSummaryRead,
    SkillContentRead,
    SkillRecord,
    SkillRecordRead,
    SkillRecordStatus,
    to_skill_record_read,
)
from app.db.repositories import SkillRepository
from app.db.session import get_db_session

from .models import ParsedSkillRecordData, SkillScanRoot
from .parser import parse_skill_file, read_skill_markdown
from .scanner import default_skill_scan_roots, scan_skill_files


class SkillServiceError(Exception):
    pass


class SkillLookupError(SkillServiceError):
    pass


class SkillContentReadError(SkillServiceError):
    pass


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

    def list_loaded_skills_for_agent(self) -> list[SkillAgentSummaryRead]:
        summaries: list[SkillAgentSummaryRead] = []
        for record in self._repository.list_skills():
            if record.status != SkillRecordStatus.LOADED:
                continue
            summaries.append(
                SkillAgentSummaryRead(
                    id=record.id,
                    name=record.name,
                    directory_name=record.directory_name,
                    description=record.description,
                    compatibility=list(record.compatibility_json),
                    entry_file=record.entry_file,
                )
            )
        return summaries

    def find_skill_by_name_or_directory_name(self, name_or_slug: str) -> SkillRecordRead | None:
        record = self._find_skill_record_by_identifier(name_or_slug, loaded_only=True)
        if record is None:
            return None
        return to_skill_record_read(record)

    def read_skill_content(self, skill_id: str) -> str:
        record = self._repository.get_skill(skill_id)
        if record is None:
            raise SkillLookupError("Skill not found.")
        return self._read_skill_entry_file(record.entry_file)

    def get_skill_content(self, skill_id: str) -> SkillContentRead | None:
        record = self._repository.get_skill(skill_id)
        if record is None:
            return None
        return self._build_skill_content(record)

    def read_skill_content_by_name_or_directory_name(self, name_or_slug: str) -> SkillContentRead:
        record = self._find_skill_record_by_identifier(name_or_slug, loaded_only=True)
        if record is None:
            raise SkillLookupError(f"Skill '{name_or_slug}' not found among loaded skills.")
        return self._build_skill_content(record)

    def rescan_skills(self) -> list[SkillRecordRead]:
        records = [self._to_skill_record(parsed) for parsed in self._scan_and_parse()]
        self._repository.replace_all(records)
        return self.list_skills()

    def _scan_and_parse(self) -> list[ParsedSkillRecordData]:
        discovered_files = scan_skill_files(resolve_skill_scan_roots(self._settings))
        return [parse_skill_file(discovered_file) for discovered_file in discovered_files]

    def _find_skill_record_by_identifier(
        self,
        identifier: str,
        *,
        loaded_only: bool,
    ) -> SkillRecord | None:
        normalized_identifier = identifier.strip()
        if not normalized_identifier:
            return None

        records = self._repository.list_skills()
        if loaded_only:
            records = [record for record in records if record.status == SkillRecordStatus.LOADED]

        for record in records:
            if record.id == normalized_identifier:
                return record

        normalized_casefold = normalized_identifier.casefold()
        for field_name in ("directory_name", "name"):
            for record in records:
                value = getattr(record, field_name, None)
                if isinstance(value, str) and value.casefold() == normalized_casefold:
                    return record
        return None

    def _build_skill_content(self, record: SkillRecord) -> SkillContentRead:
        return SkillContentRead(
            id=record.id,
            name=record.name,
            directory_name=record.directory_name,
            entry_file=record.entry_file,
            content=self._read_skill_entry_file(record.entry_file),
        )

    @staticmethod
    def _read_skill_entry_file(entry_file: str) -> str:
        entry_path = Path(entry_file)
        try:
            return read_skill_markdown(str(entry_path))
        except OSError as exc:
            raise SkillContentReadError(
                f"Failed to read skill content from '{entry_path.as_posix()}'."
            ) from exc

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
