from __future__ import annotations

import re
from pathlib import Path

from app.agent.token_budget import estimate_token_count, truncate_text_to_token_budget
from app.compat.skills.governance_config import (
    BACKGROUND_HINTS,
    CORE_RULE_HINTS,
    DEFAULT_THRESHOLDS,
    EXAMPLE_HINTS,
    TEMPLATE_HINTS,
)
from app.compat.skills.governance_models import (
    GovernedSkill,
    SkillBodyParagraphKind,
    SkillReductionResult,
    SkillReductionSection,
)

_SENTENCE_RE = re.compile(r"(?<=[。！？.!?])\s+")


def reduce_governed_skill(
    skill: GovernedSkill,
    *,
    max_description_tokens: int = DEFAULT_THRESHOLDS.description_target_max_tokens,
    max_body_tokens: int = DEFAULT_THRESHOLDS.body_target_max_tokens,
) -> SkillReductionResult:
    entry_markdown = _strip_frontmatter(skill)
    classified_sections, deduplicated_reference_paths = classify_skill_body(skill, entry_markdown)
    reduced_description = _normalize_description(
        skill=skill,
        classified_sections=classified_sections,
        max_description_tokens=max_description_tokens,
    )
    reduced_body = truncate_text_to_token_budget(
        _build_reduced_body(classified_sections),
        max_body_tokens,
    )
    return SkillReductionResult(
        skill_id=skill.governance_id,
        reduced_description=reduced_description,
        reduced_body=reduced_body,
        original_description_tokens=estimate_token_count(skill.parsed_record.description),
        reduced_description_tokens=estimate_token_count(reduced_description),
        original_body_tokens=estimate_token_count(entry_markdown),
        reduced_body_tokens=estimate_token_count(reduced_body),
        sections=classified_sections,
        deduplicated_reference_paths=deduplicated_reference_paths,
    )


def classify_skill_body(
    skill: GovernedSkill, entry_markdown: str
) -> tuple[list[SkillReductionSection], list[str]]:
    paragraphs = [
        paragraph.strip() for paragraph in entry_markdown.split("\n\n") if paragraph.strip()
    ]
    reference_paragraph_index = _build_reference_paragraph_index(skill)
    seen_paragraphs: set[str] = set()
    sections: list[SkillReductionSection] = []
    deduplicated_reference_paths: set[str] = set()
    for index, paragraph in enumerate(paragraphs):
        normalized = _normalize_text(paragraph)
        duplicate_reference = reference_paragraph_index.get(normalized)
        if duplicate_reference is not None:
            kind = SkillBodyParagraphKind.REDUNDANT
            deduplicated_reference_paths.add(duplicate_reference)
        elif normalized in seen_paragraphs:
            kind = SkillBodyParagraphKind.REDUNDANT
            duplicate_reference = "body"
        elif _looks_like_template(paragraph):
            kind = SkillBodyParagraphKind.TEMPLATE
        elif _looks_like_example(paragraph):
            kind = SkillBodyParagraphKind.EXAMPLE
        elif _looks_like_background(paragraph):
            kind = SkillBodyParagraphKind.BACKGROUND
        else:
            kind = SkillBodyParagraphKind.CORE_RULE
        sections.append(
            SkillReductionSection(
                classification=kind,
                text=paragraph,
                source_index=index,
                duplicate_of=duplicate_reference,
            )
        )
        seen_paragraphs.add(normalized)
    if not any(section.classification is SkillBodyParagraphKind.CORE_RULE for section in sections):
        for section in sections:
            if section.classification in {
                SkillBodyParagraphKind.EXAMPLE,
                SkillBodyParagraphKind.TEMPLATE,
            }:
                section.classification = SkillBodyParagraphKind.CORE_RULE
                break
    return sections, sorted(deduplicated_reference_paths)


def restore_reduction(
    reduction: SkillReductionResult,
    *,
    source_text: str,
    missing_terms: list[str],
) -> SkillReductionResult:
    restored_snippets = list(reduction.restored_snippets)
    combined_body = reduction.reduced_body
    normalized_body = combined_body.casefold()
    for term in missing_terms:
        normalized_term = term.casefold()
        if normalized_term in normalized_body:
            continue
        snippet = _find_restore_snippet(source_text, normalized_term)
        if snippet is None:
            continue
        restored_snippets.append(snippet)
        combined_body = f"{combined_body}\n\n{snippet}".strip()
        normalized_body = combined_body.casefold()
    return SkillReductionResult(
        skill_id=reduction.skill_id,
        reduced_description=reduction.reduced_description,
        reduced_body=combined_body,
        restored_snippets=restored_snippets,
        original_description_tokens=reduction.original_description_tokens,
        reduced_description_tokens=reduction.reduced_description_tokens,
        original_body_tokens=reduction.original_body_tokens,
        reduced_body_tokens=estimate_token_count(combined_body),
        sections=list(reduction.sections),
        deduplicated_reference_paths=list(reduction.deduplicated_reference_paths),
    )


def _find_restore_snippet(source_text: str, term: str) -> str | None:
    for paragraph in source_text.split("\n\n"):
        stripped = paragraph.strip()
        if stripped and term in stripped.casefold():
            return stripped
    for sentence in _SENTENCE_RE.split(source_text):
        stripped = sentence.strip()
        if stripped and term in stripped.casefold():
            return stripped
    return None


def _build_reduced_body(sections: list[SkillReductionSection]) -> str:
    selected_sections = [
        section.text
        for section in sections
        if section.classification is SkillBodyParagraphKind.CORE_RULE
    ]
    if not selected_sections:
        selected_sections = [section.text for section in sections[:4]]
    return "\n\n".join(selected_sections)


def _normalize_description(
    *,
    skill: GovernedSkill,
    classified_sections: list[SkillReductionSection],
    max_description_tokens: int,
) -> str:
    existing = skill.parsed_record.description.strip()
    differentiator = f"Especially relevant for {skill.family}." if skill.family else ""
    core_text = " ".join(
        section.text
        for section in classified_sections
        if section.classification is SkillBodyParagraphKind.CORE_RULE
    )
    triggers = _first_sentences(core_text or existing, limit=2)
    candidate = " ".join(part for part in [existing, triggers, differentiator] if part).strip()
    if estimate_token_count(candidate) < DEFAULT_THRESHOLDS.description_min_tokens:
        candidate = " ".join(
            part
            for part in [skill.directory_name.replace("-", " "), existing, triggers, differentiator]
            if part
        ).strip()
    return truncate_text_to_token_budget(candidate, max_description_tokens)


def _first_sentences(text: str, *, limit: int) -> str:
    sentences = [sentence.strip() for sentence in _SENTENCE_RE.split(text) if sentence.strip()]
    return " ".join(sentences[:limit])


def _build_reference_paragraph_index(skill: GovernedSkill) -> dict[str, str]:
    index: dict[str, str] = {}
    for reference in skill.references:
        for paragraph in reference.content.split("\n\n"):
            normalized = _normalize_text(paragraph)
            if normalized:
                index.setdefault(normalized, reference.relative_path)
    return index


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _looks_like_background(paragraph: str) -> bool:
    normalized = paragraph.casefold()
    if paragraph.startswith("#"):
        return True
    return any(token in normalized for token in BACKGROUND_HINTS)


def _looks_like_example(paragraph: str) -> bool:
    normalized = paragraph.casefold()
    if "```" in paragraph:
        return True
    return any(token in normalized for token in EXAMPLE_HINTS)


def _looks_like_template(paragraph: str) -> bool:
    normalized = paragraph.casefold()
    return any(token in normalized for token in TEMPLATE_HINTS)


def _looks_like_core_rule(paragraph: str) -> bool:
    normalized = paragraph.casefold()
    return any(token in normalized for token in CORE_RULE_HINTS)


def _strip_frontmatter(skill: GovernedSkill) -> str:
    raw_text = Path(skill.entry_file).read_text(encoding="utf-8")
    lines = raw_text.splitlines()
    if lines and lines[0].strip() == "---":
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                return "\n".join(lines[index + 1 :]).strip()
    return raw_text.strip()
