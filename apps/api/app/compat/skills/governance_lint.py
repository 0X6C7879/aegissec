from __future__ import annotations

import re
from pathlib import Path

from app.agent.token_budget import estimate_token_count
from app.compat.skills.governance_config import (
    DEFAULT_THRESHOLDS,
    PLACEHOLDER_FILE_HINTS,
    PLACEHOLDER_TEXT_HINTS,
)
from app.compat.skills.governance_discovery import validate_reference_document
from app.compat.skills.governance_models import (
    GovernanceLintIssue,
    GovernedSkill,
    SkillDiscoveryIssue,
    SkillRegistryEntry,
)
from app.compat.skills.governance_registry import index_registry_by_path

_WORD_PLACEHOLDER_RE = re.compile(
    "|".join(re.escape(token) for token in PLACEHOLDER_TEXT_HINTS), re.IGNORECASE
)


def lint_governed_skills(
    *,
    skills: list[GovernedSkill],
    registry_entries: list[SkillRegistryEntry],
    discovery_issues: list[SkillDiscoveryIssue],
    strict: bool,
) -> list[GovernanceLintIssue]:
    registry_by_path = index_registry_by_path(registry_entries)
    issues: list[GovernanceLintIssue] = [
        GovernanceLintIssue(
            level="warning",
            code="irregular_skill_layout",
            message=issue.reason,
            path=issue.relative_path,
        )
        for issue in discovery_issues
    ]

    for skill in skills:
        raw_text = Path(skill.entry_file).read_text(encoding="utf-8")
        registry_entry = registry_by_path.get(skill.relative_path.casefold())
        issues.extend(
            _lint_single_skill(
                skill=skill,
                raw_text=raw_text,
                registry_entry=registry_entry,
                strict=strict,
            )
        )

    return issues


def _lint_single_skill(
    *,
    skill: GovernedSkill,
    raw_text: str,
    registry_entry: SkillRegistryEntry | None,
    strict: bool,
) -> list[GovernanceLintIssue]:
    issues: list[GovernanceLintIssue] = []
    level_for_registry_gap = "error" if strict else "warning"
    if registry_entry is None:
        issues.append(
            GovernanceLintIssue(
                level=level_for_registry_gap,
                code="missing_registry_entry",
                message="No registry entry exists for this skill path.",
                skill_id=skill.governance_id,
                path=skill.relative_path,
            )
        )
    elif skill.family is not None and registry_entry.family != skill.family:
        issues.append(
            GovernanceLintIssue(
                level="warning",
                code="family_mismatch",
                message="Registry family does not match the discovered skill family.",
                skill_id=skill.governance_id,
                path=skill.relative_path,
            )
        )

    if skill.parsed_record.error_message:
        issues.append(
            GovernanceLintIssue(
                level="error",
                code="invalid_frontmatter",
                message=skill.parsed_record.error_message,
                skill_id=skill.governance_id,
                path=skill.relative_path,
            )
        )

    description = skill.parsed_record.description.strip()
    if not description:
        issues.append(
            GovernanceLintIssue(
                level="error",
                code="missing_description",
                message="Skill description is missing.",
                skill_id=skill.governance_id,
                path=skill.relative_path,
            )
        )
    description_tokens = estimate_token_count(description)
    if description_tokens < DEFAULT_THRESHOLDS.description_min_tokens:
        issues.append(
            GovernanceLintIssue(
                level="warning",
                code="description_too_short",
                message=(
                    "Description token count is below the recommended minimum of "
                    f"{DEFAULT_THRESHOLDS.description_min_tokens}."
                ),
                skill_id=skill.governance_id,
                path=skill.relative_path,
            )
        )
    if description_tokens > DEFAULT_THRESHOLDS.description_warn_max_tokens:
        issues.append(
            GovernanceLintIssue(
                level="warning",
                code="description_too_long",
                message=(
                    "Description token count is above the recommended soft cap of "
                    f"{DEFAULT_THRESHOLDS.description_warn_max_tokens}."
                ),
                skill_id=skill.governance_id,
                path=skill.relative_path,
            )
        )

    body_line_count = len(raw_text.splitlines())
    if body_line_count > DEFAULT_THRESHOLDS.body_hard_max_lines:
        issues.append(
            GovernanceLintIssue(
                level="warning",
                code="body_too_long",
                message=(
                    "SKILL.md exceeds the recommended line budget of "
                    f"{DEFAULT_THRESHOLDS.body_hard_max_lines}."
                ),
                skill_id=skill.governance_id,
                path=skill.relative_path,
            )
        )

    if _WORD_PLACEHOLDER_RE.search(raw_text.casefold()):
        issues.append(
            GovernanceLintIssue(
                level="warning",
                code="leftover_placeholder_text",
                message="Potential placeholder or TODO text detected in SKILL.md.",
                skill_id=skill.governance_id,
                path=skill.relative_path,
            )
        )

    issues.extend(_lint_directory_placeholders(skill))
    issues.extend(_lint_reference_documents(skill, raw_text, strict=strict))
    issues.extend(_lint_body_reference_duplication(skill, raw_text))
    return issues


def _lint_directory_placeholders(skill: GovernedSkill) -> list[GovernanceLintIssue]:
    skill_dir = Path(skill.entry_file).parent
    issues: list[GovernanceLintIssue] = []
    for candidate in skill_dir.rglob("*"):
        if candidate.is_dir() or candidate == Path(skill.entry_file):
            continue
        lowered = candidate.name.casefold()
        if any(token in lowered for token in PLACEHOLDER_FILE_HINTS):
            issues.append(
                GovernanceLintIssue(
                    level="warning",
                    code="leftover_placeholder_file",
                    message="Potential placeholder or sample file detected in skill package.",
                    skill_id=skill.governance_id,
                    path=candidate.relative_to(skill_dir).as_posix(),
                )
            )
    return issues


def _lint_reference_documents(
    skill: GovernedSkill, raw_text: str, *, strict: bool
) -> list[GovernanceLintIssue]:
    issues: list[GovernanceLintIssue] = []
    normalized_body = raw_text.casefold()
    for reference in skill.references:
        for error_code in validate_reference_document(reference):
            issues.append(
                GovernanceLintIssue(
                    level="error" if strict else "warning",
                    code=error_code,
                    message="Reference metadata is invalid or incomplete.",
                    skill_id=skill.governance_id,
                    path=reference.relative_path,
                )
            )
        if reference.relative_path.casefold() not in normalized_body and not any(
            topic.casefold() in normalized_body for topic in reference.topics
        ):
            issues.append(
                GovernanceLintIssue(
                    level="warning",
                    code="reference_not_explicitly_routed",
                    message=(
                        "Reference file is not explicitly mentioned by path or topic in SKILL.md."
                    ),
                    skill_id=skill.governance_id,
                    path=reference.relative_path,
                )
            )
    return issues


def _lint_body_reference_duplication(
    skill: GovernedSkill, raw_text: str
) -> list[GovernanceLintIssue]:
    normalized_body_paragraphs = {
        _normalize_text(paragraph)
        for paragraph in raw_text.split("\n\n")
        if _normalize_text(paragraph)
    }
    duplicated_references = [
        reference.relative_path
        for reference in skill.references
        if any(
            paragraph in normalized_body_paragraphs for paragraph in _paragraphs(reference.content)
        )
    ]
    if not duplicated_references:
        return []
    return [
        GovernanceLintIssue(
            level="warning",
            code="body_reference_duplication",
            message="Reference content duplicates material already present in SKILL.md.",
            skill_id=skill.governance_id,
            path=skill.relative_path,
            details={"reference_paths": duplicated_references},
        )
    ]


def _paragraphs(text: str) -> list[str]:
    return [
        paragraph
        for paragraph in (_normalize_text(item) for item in text.split("\n\n"))
        if paragraph
    ]


def _normalize_text(value: str) -> str:
    collapsed = re.sub(r"\s+", " ", value).strip().casefold()
    return collapsed
