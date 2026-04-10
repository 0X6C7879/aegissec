from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from app.agent.token_budget import estimate_token_count
from app.compat.skills import models as skill_models
from app.compat.skills.discovery_cache import build_discovery_provenance
from app.compat.skills.governance_config import REFERENCE_GLOB, RESERVED_DIRECT_CHILDREN
from app.compat.skills.governance_models import (
    GovernanceReferenceDocument,
    GovernedSkill,
    ReferenceCostHint,
    SkillDiscoveryIssue,
    SkillLayoutKind,
)
from app.compat.skills.parser import parse_skill_file
from app.db.models import CompatibilityScope, CompatibilitySource

_SLUG_TOKEN_RE = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class SupportedSkillPath:
    file_path: Path
    directory_name: str
    relative_path: str
    layout: SkillLayoutKind
    family: str | None


@dataclass(slots=True)
class GovernanceDiscoveryResult:
    skills: list[GovernedSkill]
    issues: list[SkillDiscoveryIssue]


def discover_supported_filesystem_skill_markdowns(
    root_path: Path,
) -> list[tuple[Path, str, str]]:
    supported, _ = classify_filesystem_skill_markdowns(root_path)
    return [
        (supported_path.file_path, supported_path.directory_name, supported_path.relative_path)
        for supported_path in supported
    ]


def classify_filesystem_skill_markdowns(
    root_path: Path,
) -> tuple[list[SupportedSkillPath], list[SkillDiscoveryIssue]]:
    supported: list[SupportedSkillPath] = []
    issues: list[SkillDiscoveryIssue] = []
    for skill_file in sorted(
        root_path.rglob("SKILL.md"), key=lambda item: item.as_posix().casefold()
    ):
        relative_path = skill_file.resolve().relative_to(root_path.resolve()).as_posix()
        classification = classify_skill_markdown_path(relative_path)
        if classification is None:
            if _should_ignore_embedded_markdown(relative_path):
                continue
            issues.append(
                SkillDiscoveryIssue(
                    relative_path=relative_path,
                    reason="unsupported_skill_layout",
                )
            )
            continue
        supported.append(
            SupportedSkillPath(
                file_path=skill_file,
                directory_name=classification.directory_name,
                relative_path=relative_path,
                layout=classification.layout,
                family=classification.family,
            )
        )
    return supported, issues


@dataclass(slots=True)
class _PathClassification:
    directory_name: str
    layout: SkillLayoutKind
    family: str | None


def classify_skill_markdown_path(relative_path: str) -> _PathClassification | None:
    parts = PurePosixPath(relative_path).parts
    if len(parts) == 2 and parts[1] == "SKILL.md":
        return _PathClassification(
            directory_name=parts[0],
            layout=SkillLayoutKind.FLAT,
            family=None,
        )
    if len(parts) == 3 and parts[2] == "SKILL.md" and parts[1] not in RESERVED_DIRECT_CHILDREN:
        return _PathClassification(
            directory_name=parts[1],
            layout=SkillLayoutKind.FAMILY_DIRECT,
            family=parts[0],
        )
    if len(parts) == 4 and parts[1] == "skills" and parts[3] == "SKILL.md":
        return _PathClassification(
            directory_name=parts[2],
            layout=SkillLayoutKind.FAMILY_NESTED,
            family=parts[0],
        )
    return None


def stable_governance_skill_id(relative_path: str) -> str:
    parts = PurePosixPath(relative_path).parts
    classification = classify_skill_markdown_path(relative_path)
    if classification is None:
        joined = "/".join(_slugify_token(part) for part in parts[:-1] if part)
        return joined or "unknown-skill"
    if classification.family is None:
        return _slugify_token(classification.directory_name)
    return (
        f"{_slugify_token(classification.family)}/{_slugify_token(classification.directory_name)}"
    )


def discover_governed_skills(root_path: Path) -> GovernanceDiscoveryResult:
    supported, issues = classify_filesystem_skill_markdowns(root_path)
    skills: list[GovernedSkill] = []
    for supported_path in supported:
        discovered_file = skill_models.DiscoveredSkillFile(
            source=CompatibilitySource.LOCAL,
            scope=CompatibilityScope.PROJECT,
            root_dir=root_path.resolve().as_posix(),
            directory_name=supported_path.directory_name,
            entry_file=supported_path.file_path.resolve().as_posix(),
            relative_path=supported_path.relative_path,
            discovery_provenance=build_discovery_provenance(
                source_root=root_path.resolve().as_posix(),
                entry_file=supported_path.file_path.resolve().as_posix(),
                relative_path=supported_path.relative_path,
                source_kind=skill_models.SkillSourceKind.FILESYSTEM.value,
                root_label="repo-skills",
                metadata={},
            ),
        )
        parsed_record = parse_skill_file(discovered_file)
        skill_dir = supported_path.file_path.parent
        skills.append(
            GovernedSkill(
                governance_id=stable_governance_skill_id(supported_path.relative_path),
                family=supported_path.family,
                layout=supported_path.layout,
                relative_path=supported_path.relative_path,
                parsed_record=parsed_record,
                references=load_reference_documents(skill_dir),
            )
        )
    return GovernanceDiscoveryResult(skills=skills, issues=issues)


def load_reference_documents(skill_dir: Path) -> list[GovernanceReferenceDocument]:
    references_dir = skill_dir / "references"
    if not references_dir.exists() or not references_dir.is_dir():
        return []

    documents: list[GovernanceReferenceDocument] = []
    for reference_file in sorted(
        references_dir.glob(REFERENCE_GLOB), key=lambda item: item.as_posix().casefold()
    ):
        if reference_file.is_dir():
            continue
        metadata, body = _split_optional_frontmatter(reference_file.read_text(encoding="utf-8"))
        when_value = metadata.get("when")
        topics_value = metadata.get("topics")
        cost_hint_value = metadata.get("cost_hint")
        topics = _normalize_reference_topics(topics_value)
        reference_title = _first_reference_heading(body)
        if not topics:
            topics = _infer_reference_topics(
                reference_file=reference_file,
                body=body,
                reference_title=reference_title,
            )
        when_text = (
            when_value.strip() if isinstance(when_value, str) and when_value.strip() else None
        )
        if when_text is None:
            when_text = _infer_reference_when(
                reference_file=reference_file,
                reference_title=reference_title,
                topics=topics,
            )
        if isinstance(cost_hint_value, str):
            normalized_cost = cost_hint_value.strip().casefold()
            cost_hint = (
                ReferenceCostHint(normalized_cost)
                if normalized_cost in ReferenceCostHint._value2member_map_
                else ReferenceCostHint.UNKNOWN
            )
        else:
            cost_hint = ReferenceCostHint.UNKNOWN
        if cost_hint is ReferenceCostHint.UNKNOWN:
            cost_hint = _infer_reference_cost_hint(body)
        enriched_metadata = dict(metadata)
        enriched_metadata.setdefault("when", when_text)
        enriched_metadata.setdefault("topics", list(topics))
        enriched_metadata.setdefault("cost_hint", cost_hint.value)
        documents.append(
            GovernanceReferenceDocument(
                path=reference_file.resolve().as_posix(),
                relative_path=reference_file.relative_to(skill_dir).as_posix(),
                when=when_text,
                topics=topics,
                cost_hint=cost_hint,
                content=body,
                metadata=enriched_metadata,
            )
        )
    return documents


def validate_reference_document(document: GovernanceReferenceDocument) -> list[str]:
    errors: list[str] = []
    if document.when is None:
        errors.append("reference_missing_when")
    if not document.topics:
        errors.append("reference_missing_topics")
    if any(not topic for topic in document.topics):
        errors.append("reference_topics_must_not_be_empty")
    if document.cost_hint is ReferenceCostHint.UNKNOWN:
        errors.append("reference_missing_cost_hint")
    return errors


def build_skill_token_summary(skill: GovernedSkill) -> dict[str, int]:
    content = Path(skill.entry_file).read_text(encoding="utf-8")
    reference_tokens = sum(
        estimate_token_count(reference.content) for reference in skill.references
    )
    return {
        "description_tokens": estimate_token_count(skill.parsed_record.description),
        "body_tokens": estimate_token_count(content),
        "reference_tokens": reference_tokens,
    }


def _split_optional_frontmatter(text: str) -> tuple[dict[str, object], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text.strip()
    closing_index: int | None = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_index = index
            break
    if closing_index is None:
        return {}, text.strip()
    frontmatter_text = "\n".join(lines[1:closing_index])
    try:
        loaded = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError:
        loaded = None
    frontmatter = loaded if isinstance(loaded, dict) else {}
    body = "\n".join(lines[closing_index + 1 :]).strip()
    return frontmatter, body


def _should_ignore_embedded_markdown(relative_path: str) -> bool:
    parts = PurePosixPath(relative_path).parts
    return any(part in RESERVED_DIRECT_CHILDREN for part in parts[1:-1])


def _normalize_reference_topics(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return _dedupe_reference_topics(
        [item.strip() for item in value if isinstance(item, str) and item.strip()]
    )


def _infer_reference_topics(
    *,
    reference_file: Path,
    body: str,
    reference_title: str | None,
) -> list[str]:
    candidates: list[str] = []
    stem = reference_file.stem
    candidates.extend([stem, stem.replace("_", "-"), stem.replace("_", " ")])
    if reference_title:
        candidates.extend(
            [
                reference_title,
                reference_title.replace("/", " "),
                reference_title.replace("-", " "),
            ]
        )
    first_paragraph = next((line.strip() for line in body.splitlines() if line.strip()), "")
    if first_paragraph and not first_paragraph.startswith("#"):
        candidates.append(first_paragraph)
    return _dedupe_reference_topics(candidates)


def _dedupe_reference_topics(values: list[str] | tuple[str, ...]) -> list[str]:
    normalized_topics: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        if not isinstance(raw_value, str):
            continue
        for candidate in _topic_variants(raw_value):
            canonical = re.sub(r"[\s_-]+", " ", candidate).strip().casefold()
            if len(canonical) < 2 or canonical in seen:
                continue
            seen.add(canonical)
            normalized_topics.append(candidate)
    return normalized_topics


def _topic_variants(value: str) -> list[str]:
    stripped = value.strip().strip("#").strip()
    if not stripped:
        return []
    collapsed = re.sub(r"\s+", " ", stripped)
    variants = [collapsed]
    if "_" in collapsed or "-" in collapsed:
        variants.append(collapsed.replace("_", "-"))
        variants.append(collapsed.replace("_", " ").replace("-", " "))
    return variants


def _first_reference_heading(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return None


def _infer_reference_when(
    *,
    reference_file: Path,
    reference_title: str | None,
    topics: list[str],
) -> str:
    normalized_stem = reference_file.stem.casefold()
    if "background" in normalized_stem:
        return (
            "Read when the task needs background context, domain framing, "
            "or supporting rationale."
        )
    if "example" in normalized_stem:
        return "Read when the task needs worked examples, payload samples, or concrete cases."
    if "template" in normalized_stem or "output" in normalized_stem:
        return (
            "Read when the task needs output structure, report templates, "
            "or reusable response scaffolds."
        )
    if "schema" in normalized_stem:
        return (
            "Read when the task needs field definitions, protocol details, "
            "or structural constraints."
        )
    if "faq" in normalized_stem:
        return (
            "Read when the task hits edge cases, troubleshooting branches, "
            "or repeated operator questions."
        )
    label = topics[0] if topics else reference_title or reference_file.stem.replace("_", " ")
    return f"Read when the task needs detailed guidance for {label}."


def _infer_reference_cost_hint(body: str) -> ReferenceCostHint:
    token_count = estimate_token_count(body)
    if token_count <= 256:
        return ReferenceCostHint.LOW
    if token_count <= 1024:
        return ReferenceCostHint.MEDIUM
    return ReferenceCostHint.HIGH


def _slugify_token(value: str) -> str:
    lowered = value.strip().casefold()
    normalized = _SLUG_TOKEN_RE.sub("-", lowered).strip("-")
    return normalized or "skill"
