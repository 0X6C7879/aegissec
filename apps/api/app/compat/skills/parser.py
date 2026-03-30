from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import yaml

from app.db.models import SkillRecordStatus

from .models import DiscoveredSkillFile, ParsedSkillRecordData

KNOWN_FRONTMATTER_FIELDS = {"name", "description", "compatibility", "metadata"}
SIMPLE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$", re.IGNORECASE)


def parse_skill_file(discovered_file: DiscoveredSkillFile) -> ParsedSkillRecordData:
    entry_path = Path(discovered_file.entry_file)
    text = entry_path.read_text(encoding="utf-8")
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    now = datetime.now(UTC)

    try:
        frontmatter, body = _split_frontmatter(text)
        name = _extract_name(frontmatter, discovered_file.directory_name)
        description = _extract_description(frontmatter, body)
        compatibility = _extract_compatibility(frontmatter)
        metadata = _extract_metadata(frontmatter)
        raw_frontmatter = {
            key: value for key, value in frontmatter.items() if key not in KNOWN_FRONTMATTER_FIELDS
        }
        error_message = _build_validation_error(
            frontmatter=frontmatter,
            directory_name=discovered_file.directory_name,
        )
        status = SkillRecordStatus.INVALID if error_message else SkillRecordStatus.LOADED
    except SkillParseError as exc:
        name = discovered_file.directory_name
        description = ""
        compatibility = []
        metadata = {}
        raw_frontmatter = {}
        error_message = str(exc)
        status = SkillRecordStatus.INVALID

    return ParsedSkillRecordData(
        id=str(uuid5(NAMESPACE_URL, discovered_file.entry_file)),
        source=discovered_file.source,
        scope=discovered_file.scope,
        root_dir=discovered_file.root_dir,
        directory_name=discovered_file.directory_name,
        entry_file=discovered_file.entry_file,
        name=name,
        description=description,
        compatibility=compatibility,
        metadata=metadata,
        raw_frontmatter=raw_frontmatter,
        status=status,
        error_message=error_message,
        content_hash=content_hash,
        last_scanned_at=now,
    )


class SkillParseError(Exception):
    pass


def _split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    closing_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break

    if closing_index is None:
        raise SkillParseError("YAML frontmatter is missing a closing '---' delimiter.")

    frontmatter_text = "\n".join(lines[1:closing_index]).strip()
    body = "\n".join(lines[closing_index + 1 :]).strip()
    if not frontmatter_text:
        return {}, body

    try:
        parsed = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError as exc:
        raise SkillParseError(f"Invalid YAML frontmatter: {exc}") from exc

    if parsed is None:
        return {}, body
    if not isinstance(parsed, dict):
        raise SkillParseError("YAML frontmatter must decode to a mapping object.")

    normalized: dict[str, object] = {}
    for key, value in parsed.items():
        if not isinstance(key, str):
            raise SkillParseError("YAML frontmatter keys must be strings.")
        normalized[key] = value
    return normalized, body


def _extract_name(frontmatter: dict[str, object], directory_name: str) -> str:
    raw_name = frontmatter.get("name")
    if raw_name is None:
        return directory_name
    if not isinstance(raw_name, str) or not raw_name.strip():
        raise SkillParseError("Frontmatter field 'name' must be a non-empty string.")
    return raw_name.strip()


def _extract_description(frontmatter: dict[str, object], body: str) -> str:
    raw_description = frontmatter.get("description")
    if raw_description is not None:
        if not isinstance(raw_description, str):
            raise SkillParseError("Frontmatter field 'description' must be a string.")
        return raw_description.strip()

    for line in body.splitlines():
        stripped_line = line.strip()
        if stripped_line and not stripped_line.startswith("#"):
            return stripped_line
    return ""


def _extract_compatibility(frontmatter: dict[str, object]) -> list[str]:
    raw_compatibility = frontmatter.get("compatibility")
    if raw_compatibility is None:
        return []
    if isinstance(raw_compatibility, str):
        return [raw_compatibility.strip()] if raw_compatibility.strip() else []
    if isinstance(raw_compatibility, list):
        values: list[str] = []
        for item in raw_compatibility:
            if not isinstance(item, str):
                raise SkillParseError(
                    "Frontmatter field 'compatibility' must be a string or string array."
                )
            stripped_item = item.strip()
            if stripped_item:
                values.append(stripped_item)
        return values
    raise SkillParseError("Frontmatter field 'compatibility' must be a string or string array.")


def _extract_metadata(frontmatter: dict[str, object]) -> dict[str, object]:
    raw_metadata = frontmatter.get("metadata")
    if raw_metadata is None:
        return {}
    if not isinstance(raw_metadata, dict):
        raise SkillParseError("Frontmatter field 'metadata' must be an object.")

    normalized: dict[str, object] = {}
    for key, value in raw_metadata.items():
        if not isinstance(key, str):
            raise SkillParseError("Frontmatter metadata keys must be strings.")
        normalized[key] = value
    return normalized


def _build_validation_error(*, frontmatter: dict[str, object], directory_name: str) -> str | None:
    raw_name = frontmatter.get("name")
    if not isinstance(raw_name, str):
        return None

    stripped_name = raw_name.strip()
    if not stripped_name or not SIMPLE_NAME_PATTERN.fullmatch(stripped_name):
        return None
    if stripped_name != directory_name:
        return "Frontmatter field 'name' must match the skill directory name for slug-like names."
    return None
