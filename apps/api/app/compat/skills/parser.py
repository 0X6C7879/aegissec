from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar
from uuid import NAMESPACE_URL, uuid5

import yaml

from app.db.models import SkillRecordStatus

from .discovery_cache import build_discovery_provenance, canonicalize_skill_path
from .models import (
    DiscoveredSkillFile,
    ParsedSkillFrontmatter,
    ParsedSkillRecordData,
    SkillSourceIdentity,
)
from .orchestration_models import (
    SkillExecutionPolicy,
    SkillOrchestrationHints,
    SkillResultSchema,
)
from .preflight import SkillPreflightCheck
from .trust import SkillTrustMetadata, resolve_effective_trust_level

KNOWN_FRONTMATTER_FIELDS = {
    "name",
    "description",
    "compatibility",
    "metadata",
    "parameter_schema",
    "parameters",
    "input_schema",
    "aliases",
    "user_invocable",
    "user-invocable",
    "allowed_tools",
    "allowed-tools",
    "argument_hint",
    "argument-hint",
    "paths",
    "when_to_use",
    "when-to-use",
    "context",
    "agent",
    "effort",
    "version",
    "model_hint",
    "model-hint",
    "verification_mode",
    "verification-mode",
    "shell_profile",
    "shell-profile",
    "trust_level",
    "trust-level",
    "preflight_checks",
    "preflight-checks",
    "orchestration_role",
    "orchestration-role",
    "orchestration_hints",
    "orchestration-hints",
    "fanout_group",
    "fanout-group",
    "preferred_stage",
    "preferred-stage",
    "context_strategy",
    "context-strategy",
    "execution_policy",
    "execution-policy",
    "result_schema",
    "result-schema",
    "family",
    "domain",
    "task_mode",
    "task-mode",
    "tags",
}
SIMPLE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$", re.IGNORECASE)
StructuredMetadataT = TypeVar(
    "StructuredMetadataT",
    SkillOrchestrationHints,
    SkillExecutionPolicy,
    SkillResultSchema,
)


def read_skill_markdown(entry_file: str) -> str:
    return Path(entry_file).read_text(encoding="utf-8")


def parse_skill_file(discovered_file: DiscoveredSkillFile) -> ParsedSkillRecordData:
    entry_path = Path(discovered_file.entry_file)
    text = read_skill_markdown(str(entry_path))
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    now = datetime.now(UTC)
    relative_path = discovered_file.relative_path or entry_path.name
    source_identity = SkillSourceIdentity(
        source_kind=discovered_file.source_kind,
        source=discovered_file.source,
        scope=discovered_file.scope,
        source_root=discovered_file.root_dir,
        relative_path=relative_path,
        fingerprint=content_hash,
        canonical_source_root=canonicalize_skill_path(discovered_file.root_dir),
        canonical_entry_file=canonicalize_skill_path(discovered_file.entry_file),
        discovery_provenance=(
            dict(discovered_file.discovery_provenance)
            if discovered_file.discovery_provenance
            else build_discovery_provenance(
                source_root=discovered_file.root_dir,
                entry_file=discovered_file.entry_file,
                relative_path=relative_path,
                source_kind=discovered_file.source_kind.value,
                root_label=discovered_file.root_label,
                metadata=discovered_file.metadata,
            )
        ),
    )

    try:
        parsed_frontmatter = parse_skill_frontmatter(
            text,
            directory_name=discovered_file.directory_name,
        )
        status = (
            SkillRecordStatus.INVALID
            if parsed_frontmatter.validation_error is not None
            else SkillRecordStatus.LOADED
        )
    except SkillParseError as exc:
        parsed_frontmatter = ParsedSkillFrontmatter(
            name=discovered_file.directory_name,
            description="",
            compatibility=[],
            metadata={},
            parameter_schema={},
            raw_frontmatter={},
            validation_error=str(exc),
        )
        status = SkillRecordStatus.INVALID

    return ParsedSkillRecordData(
        id=str(uuid5(NAMESPACE_URL, discovered_file.entry_file)),
        source=discovered_file.source,
        scope=discovered_file.scope,
        root_dir=discovered_file.root_dir,
        directory_name=discovered_file.directory_name,
        entry_file=discovered_file.entry_file,
        name=parsed_frontmatter.name,
        description=parsed_frontmatter.description,
        compatibility=parsed_frontmatter.compatibility,
        metadata=parsed_frontmatter.metadata,
        parameter_schema=parsed_frontmatter.parameter_schema,
        aliases=parsed_frontmatter.aliases,
        user_invocable=parsed_frontmatter.user_invocable,
        allowed_tools=parsed_frontmatter.allowed_tools,
        argument_hint=parsed_frontmatter.argument_hint,
        activation_paths=parsed_frontmatter.activation_paths,
        when_to_use=parsed_frontmatter.when_to_use,
        context_hint=parsed_frontmatter.context_hint,
        agent=parsed_frontmatter.agent,
        effort=parsed_frontmatter.effort,
        version=parsed_frontmatter.version,
        model_hint=parsed_frontmatter.model_hint,
        trust_metadata=_build_trust_metadata(parsed_frontmatter, discovered_file),
        preflight_checks=list(parsed_frontmatter.preflight_checks),
        orchestration_role=parsed_frontmatter.orchestration_role,
        orchestration_hints=parsed_frontmatter.orchestration_hints,
        fanout_group=parsed_frontmatter.fanout_group,
        preferred_stage=parsed_frontmatter.preferred_stage,
        context_strategy=parsed_frontmatter.context_strategy,
        execution_policy=parsed_frontmatter.execution_policy,
        result_schema=parsed_frontmatter.result_schema,
        semantic_family=parsed_frontmatter.semantic_family,
        semantic_domain=parsed_frontmatter.semantic_domain,
        semantic_task_mode=parsed_frontmatter.semantic_task_mode,
        semantic_tags=parsed_frontmatter.semantic_tags,
        status=status,
        enabled=True,
        error_message=parsed_frontmatter.validation_error,
        content_hash=content_hash,
        last_scanned_at=now,
        source_identity=source_identity,
        root_label=discovered_file.root_label,
        discovery_provenance=dict(source_identity.discovery_provenance),
        raw_frontmatter=_with_compat_metadata(
            parsed_frontmatter.raw_frontmatter,
            activation_paths=parsed_frontmatter.activation_paths,
        ),
    )


class SkillParseError(Exception):
    pass


def parse_skill_frontmatter(text: str, *, directory_name: str) -> ParsedSkillFrontmatter:
    frontmatter, body = _split_frontmatter(text)
    name = _extract_name(frontmatter, directory_name)
    description = _extract_description(frontmatter, body)
    compatibility = _extract_compatibility(frontmatter)
    metadata = _extract_metadata(frontmatter)
    parameter_schema = _extract_parameter_schema(frontmatter)
    raw_frontmatter = {
        key: value for key, value in frontmatter.items() if key not in KNOWN_FRONTMATTER_FIELDS
    }
    return ParsedSkillFrontmatter(
        name=name,
        description=description,
        compatibility=compatibility,
        metadata=metadata,
        parameter_schema=parameter_schema,
        raw_frontmatter=raw_frontmatter,
        aliases=_extract_aliases(frontmatter),
        user_invocable=_extract_optional_bool(frontmatter, ("user_invocable", "user-invocable")),
        allowed_tools=_extract_string_list(frontmatter, ("allowed_tools", "allowed-tools")),
        argument_hint=_extract_optional_string(frontmatter, ("argument_hint", "argument-hint")),
        activation_paths=_extract_string_list(frontmatter, ("paths",)),
        when_to_use=_extract_optional_string(frontmatter, ("when_to_use", "when-to-use")),
        context_hint=_extract_optional_string(frontmatter, ("context",)),
        agent=_extract_optional_string(frontmatter, ("agent",)),
        effort=_extract_optional_string(frontmatter, ("effort",)),
        version=_extract_optional_string(frontmatter, ("version",)),
        model_hint=_extract_optional_string(frontmatter, ("model_hint", "model-hint")),
        verification_mode=_extract_optional_string(
            frontmatter,
            ("verification_mode", "verification-mode"),
        ),
        shell_profile=_extract_optional_string(frontmatter, ("shell_profile", "shell-profile")),
        trust_level=_extract_optional_string(frontmatter, ("trust_level", "trust-level")),
        preflight_checks=_extract_preflight_checks(frontmatter),
        orchestration_role=_extract_optional_string(
            frontmatter,
            ("orchestration_role", "orchestration-role"),
        ),
        orchestration_hints=_extract_optional_structured_metadata(
            frontmatter,
            ("orchestration_hints", "orchestration-hints"),
            SkillOrchestrationHints,
        ),
        fanout_group=_extract_optional_string(frontmatter, ("fanout_group", "fanout-group")),
        preferred_stage=_extract_optional_string(
            frontmatter,
            ("preferred_stage", "preferred-stage"),
        ),
        context_strategy=_extract_optional_string(
            frontmatter,
            ("context_strategy", "context-strategy"),
        ),
        execution_policy=_extract_optional_structured_metadata(
            frontmatter,
            ("execution_policy", "execution-policy"),
            SkillExecutionPolicy,
        ),
        result_schema=_extract_optional_structured_metadata(
            frontmatter,
            ("result_schema", "result-schema"),
            SkillResultSchema,
        ),
        semantic_family=_extract_optional_string(frontmatter, ("family",)),
        semantic_domain=_extract_optional_string(frontmatter, ("domain",)),
        semantic_task_mode=_extract_optional_string(frontmatter, ("task_mode", "task-mode")),
        semantic_tags=_extract_string_list(frontmatter, ("tags",)),
        validation_error=_build_validation_error(
            frontmatter=frontmatter,
            directory_name=directory_name,
        ),
    )


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


def _extract_parameter_schema(frontmatter: dict[str, object]) -> dict[str, object]:
    for key in ("parameter_schema", "parameters", "input_schema"):
        raw_schema = frontmatter.get(key)
        if raw_schema is None:
            continue
        if not isinstance(raw_schema, dict):
            raise SkillParseError(
                "Frontmatter field 'parameter_schema' (or aliases 'parameters'/'input_schema') "
                "must be an object."
            )
        normalized: dict[str, object] = {}
        for schema_key, schema_value in raw_schema.items():
            if not isinstance(schema_key, str):
                raise SkillParseError("Skill parameter schema keys must be strings.")
            normalized[schema_key] = schema_value
        return normalized
    return {}


def _extract_aliases(frontmatter: dict[str, object]) -> list[str]:
    seen: set[str] = set()
    aliases: list[str] = []
    for alias in _extract_string_list(frontmatter, ("aliases",)):
        normalized = alias.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        aliases.append(alias)
    return aliases


def _extract_optional_string(
    frontmatter: dict[str, object],
    keys: tuple[str, ...],
) -> str | None:
    raw_value = _coalesce_frontmatter_value(frontmatter, keys)
    if raw_value is None:
        return None
    if not isinstance(raw_value, str):
        joined_keys = "/".join(keys)
        raise SkillParseError(f"Frontmatter field '{joined_keys}' must be a string.")
    stripped = raw_value.strip()
    return stripped or None


def _extract_optional_bool(
    frontmatter: dict[str, object],
    keys: tuple[str, ...],
) -> bool | None:
    raw_value = _coalesce_frontmatter_value(frontmatter, keys)
    if raw_value is None:
        return None
    if not isinstance(raw_value, bool):
        joined_keys = "/".join(keys)
        raise SkillParseError(f"Frontmatter field '{joined_keys}' must be a boolean.")
    return raw_value


def _extract_string_list(
    frontmatter: dict[str, object],
    keys: tuple[str, ...],
) -> list[str]:
    raw_value = _coalesce_frontmatter_value(frontmatter, keys)
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        stripped_value = raw_value.strip()
        return [stripped_value] if stripped_value else []
    if not isinstance(raw_value, list):
        joined_keys = "/".join(keys)
        raise SkillParseError(
            f"Frontmatter field '{joined_keys}' must be a string or string array."
        )

    values: list[str] = []
    for item in raw_value:
        if not isinstance(item, str):
            joined_keys = "/".join(keys)
            raise SkillParseError(
                f"Frontmatter field '{joined_keys}' must be a string or string array."
            )
        stripped_item = item.strip()
        if stripped_item:
            values.append(stripped_item)
    return values


def _extract_preflight_checks(frontmatter: dict[str, object]) -> list[SkillPreflightCheck]:
    raw_value = _coalesce_frontmatter_value(frontmatter, ("preflight_checks", "preflight-checks"))
    if raw_value is None:
        return []
    if not isinstance(raw_value, list):
        raise SkillParseError(
            "Frontmatter field 'preflight_checks/preflight-checks' must be an array."
        )

    checks: list[SkillPreflightCheck] = []
    for item in raw_value:
        if isinstance(item, str):
            stripped_name = item.strip()
            if stripped_name:
                checks.append(SkillPreflightCheck(name=stripped_name))
            continue
        if not isinstance(item, dict):
            raise SkillParseError(
                "Frontmatter field 'preflight_checks/preflight-checks' "
                "must contain strings or objects."
            )
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SkillParseError(
                "Each preflight check object must include a non-empty string 'name'."
            )
        kind = item.get("kind")
        required = item.get("required")
        read_only = item.get("read_only", item.get("read-only", True))
        description = item.get("description")
        if kind is not None and (not isinstance(kind, str) or not kind.strip()):
            raise SkillParseError("Preflight check field 'kind' must be a non-empty string.")
        if required is not None and not isinstance(required, bool):
            raise SkillParseError("Preflight check field 'required' must be a boolean.")
        if not isinstance(read_only, bool):
            raise SkillParseError("Preflight check field 'read_only/read-only' must be a boolean.")
        if description is not None and not isinstance(description, str):
            raise SkillParseError("Preflight check field 'description' must be a string.")
        metadata = {
            key: value
            for key, value in item.items()
            if key not in {"name", "kind", "required", "read_only", "read-only", "description"}
        }
        checks.append(
            SkillPreflightCheck(
                name=name.strip(),
                kind=(kind.strip() if isinstance(kind, str) and kind.strip() else "generic"),
                required=required if isinstance(required, bool) else True,
                read_only=read_only,
                description=description.strip()
                if isinstance(description, str) and description.strip()
                else None,
                metadata=metadata,
            )
        )
    return checks


def _extract_optional_structured_metadata[StructuredMetadataT](
    frontmatter: dict[str, object],
    keys: tuple[str, ...],
    metadata_type: Callable[[dict[str, object]], StructuredMetadataT],
) -> StructuredMetadataT | None:
    raw_value = _coalesce_frontmatter_value(frontmatter, keys)
    if raw_value is None:
        return None
    if not isinstance(raw_value, dict):
        joined_keys = "/".join(keys)
        raise SkillParseError(f"Frontmatter field '{joined_keys}' must be an object.")
    normalized: dict[str, object] = {}
    for key, value in raw_value.items():
        if not isinstance(key, str):
            joined_keys = "/".join(keys)
            raise SkillParseError(f"Frontmatter field '{joined_keys}' must use string keys.")
        normalized[key] = value
    return metadata_type(normalized)


def _coalesce_frontmatter_value(
    frontmatter: dict[str, object],
    keys: tuple[str, ...],
) -> object | None:
    found_key: str | None = None
    value: object | None = None
    for key in keys:
        if key not in frontmatter:
            continue
        if found_key is None:
            found_key = key
            value = frontmatter[key]
            continue
        if frontmatter[key] != value:
            joined_keys = "/".join(keys)
            message = (
                "Conflicting values were provided for equivalent frontmatter fields "
                f"'{joined_keys}'."
            )
            raise SkillParseError(message)
    return value


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


def _with_compat_metadata(
    raw_frontmatter: dict[str, object], *, activation_paths: list[str]
) -> dict[str, object]:
    enriched = dict(raw_frontmatter)
    compat_payload = enriched.get("_compat")
    if not isinstance(compat_payload, dict):
        compat_payload = {}
    if activation_paths:
        compat_payload["activation_paths"] = list(activation_paths)
    if compat_payload:
        enriched["_compat"] = compat_payload
    return enriched


def _build_trust_metadata(
    parsed_frontmatter: ParsedSkillFrontmatter,
    discovered_file: DiscoveredSkillFile,
) -> SkillTrustMetadata | None:
    trust_metadata = SkillTrustMetadata(
        verification_mode=parsed_frontmatter.verification_mode,
        shell_profile=parsed_frontmatter.shell_profile,
        trust_level=resolve_effective_trust_level(
            source=discovered_file.source.value,
            source_kind=discovered_file.source_kind.value,
        ),
    )
    return None if trust_metadata.is_empty else trust_metadata
