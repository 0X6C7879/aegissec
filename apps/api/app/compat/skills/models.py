from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.db.models import CompatibilityScope, CompatibilitySource, SkillRecordStatus


@dataclass(slots=True)
class SkillScanRoot:
    source: CompatibilitySource
    scope: CompatibilityScope
    root_dir: str


@dataclass(slots=True)
class DiscoveredSkillFile:
    source: CompatibilitySource
    scope: CompatibilityScope
    root_dir: str
    directory_name: str
    entry_file: str


@dataclass(slots=True)
class ParsedSkillRecordData:
    id: str
    source: CompatibilitySource
    scope: CompatibilityScope
    root_dir: str
    directory_name: str
    entry_file: str
    name: str
    description: str
    compatibility: list[str]
    metadata: dict[str, object]
    parameter_schema: dict[str, object]
    raw_frontmatter: dict[str, object]
    status: SkillRecordStatus
    enabled: bool
    error_message: str | None
    content_hash: str
    last_scanned_at: datetime
