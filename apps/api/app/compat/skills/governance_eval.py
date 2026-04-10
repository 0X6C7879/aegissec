from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from app.agent.token_budget import estimate_token_count
from app.compat.skills import models as skill_models
from app.compat.skills.compiler import compile_skill_record
from app.compat.skills.governance_discovery import (
    discover_governed_skills,
    stable_governance_skill_id,
)
from app.compat.skills.governance_models import (
    GovernanceReferenceDocument,
    GovernedSkill,
    RoutingEvaluationResult,
    RoutingTestCase,
    SkillRegistryEntry,
    SkillReductionResult,
    TaskCaseEvaluationResult,
    TaskEvalCase,
    TaskVariantResult,
)
from app.compat.skills.governance_reduce import reduce_governed_skill
from app.compat.skills.governance_reduce import (
    restore_reduction as apply_restore_reduction,
)
from app.compat.skills.governance_registry import index_registry_by_path
from app.compat.skills.resolution import resolve_skill_candidates
from app.db.models import CompatibilityScope, CompatibilitySource, SkillRecord, SkillRecordStatus


@dataclass(slots=True)
class GovernedSkillCatalog:
    skills: list[GovernedSkill]
    compiled_skills: list[skill_models.CompiledSkill]
    reduced_compiled_skills: list[skill_models.CompiledSkill]
    reductions_by_skill_id: dict[str, SkillReductionResult]


def build_governed_skill_catalog(
    skills_root: Path,
    *,
    registry_entries: list[SkillRegistryEntry] | None = None,
) -> GovernedSkillCatalog:
    discovery = discover_governed_skills(skills_root)
    registry_by_path = index_registry_by_path(registry_entries or [])
    governed_skills: list[GovernedSkill] = []
    compiled_skills: list[skill_models.CompiledSkill] = []
    reduced_compiled_skills: list[skill_models.CompiledSkill] = []
    reductions_by_skill_id: dict[str, SkillReductionResult] = {}

    for discovered_skill in discovery.skills:
        registry_entry = registry_by_path.get(discovered_skill.relative_path.casefold())
        if registry_entries is not None and registry_entry is None:
            continue
        governance_id = (
            registry_entry.skill_id
            if registry_entry is not None
            else stable_governance_skill_id(discovered_skill.relative_path)
        )
        governed_skill = GovernedSkill(
            governance_id=governance_id,
            family=registry_entry.family if registry_entry is not None else discovered_skill.family,
            layout=discovered_skill.layout,
            relative_path=discovered_skill.relative_path,
            parsed_record=discovered_skill.parsed_record,
            references=discovered_skill.references,
        )
        entry_text = Path(governed_skill.entry_file).read_text(encoding="utf-8")
        reduction = reduce_governed_skill(governed_skill)

        compiled_skills.append(compile_skill_record(_to_skill_record(governed_skill), entry_text))
        reduced_compiled_skills.append(
            compile_skill_record(
                _to_skill_record(
                    governed_skill,
                    description_override=reduction.reduced_description,
                ),
                f"{reduction.reduced_description}\n\n{reduction.reduced_body}".strip(),
            )
        )
        governed_skills.append(governed_skill)
        reductions_by_skill_id[governed_skill.governance_id] = reduction

    return GovernedSkillCatalog(
        skills=governed_skills,
        compiled_skills=compiled_skills,
        reduced_compiled_skills=reduced_compiled_skills,
        reductions_by_skill_id=reductions_by_skill_id,
    )


def evaluate_routing(
    catalog: GovernedSkillCatalog,
    cases: list[RoutingTestCase],
) -> dict[str, object]:
    original_descriptions = {
        skill.governance_id: skill.parsed_record.description for skill in catalog.skills
    }
    reduced_descriptions = {
        skill_id: reduction.reduced_description
        for skill_id, reduction in catalog.reductions_by_skill_id.items()
    }
    original_report = _evaluate_routing_variant(
        descriptions=original_descriptions,
        cases=cases,
        variant="original",
    )
    reduced_report = _evaluate_routing_variant(
        descriptions=reduced_descriptions,
        cases=cases,
        variant="reduced",
    )
    original_pass_rate = _coerce_float(original_report.get("routing_pass_rate"))
    reduced_pass_rate = _coerce_float(reduced_report.get("routing_pass_rate"))
    return {
        "total_cases": reduced_report["total_cases"],
        "pass_count": reduced_report["pass_count"],
        "routing_pass_rate": reduced_report["routing_pass_rate"],
        "precision": reduced_report["precision"],
        "recall": reduced_report["recall"],
        "results": reduced_report["results"],
        "confusion_matrix": reduced_report["confusion_matrix"],
        "neighbor_confusion_count": reduced_report["neighbor_confusion_count"],
        "shadow_interference_count": reduced_report["shadow_interference_count"],
        "before_after_delta": reduced_pass_rate - original_pass_rate,
        "original": original_report,
        "reduced": reduced_report,
    }


def _evaluate_routing_variant(
    *,
    descriptions: dict[str, str],
    cases: list[RoutingTestCase],
    variant: str,
) -> dict[str, object]:
    results: list[RoutingEvaluationResult] = []
    confusion: dict[str, dict[str, int]] = {}
    neighbor_confusion_count = 0
    shadow_interference_count = 0

    for case in cases:
        selected_skill_id = _select_description_candidate(
            prompt=case.prompt,
            descriptions=descriptions,
        )
        passed = selected_skill_id == case.expected_skill_id
        results.append(
            RoutingEvaluationResult(
                case_id=case.case_id,
                expected_skill_id=case.expected_skill_id,
                selected_skill_id=selected_skill_id,
                passed=passed,
                variant=variant,
                scenario_type=case.scenario_type,
            )
        )
        expected_bucket = confusion.setdefault(case.expected_skill_id, {})
        confusion_key = selected_skill_id or "<none>"
        expected_bucket[confusion_key] = expected_bucket.get(confusion_key, 0) + 1
        if case.scenario_type == "neighbor" and not passed:
            neighbor_confusion_count += 1
        if case.scenario_type == "shadow" and not passed:
            shadow_interference_count += 1

    pass_count = sum(1 for result in results if result.passed)
    total_cases = len(results)
    pass_rate = 1.0 if total_cases == 0 else pass_count / total_cases
    return {
        "total_cases": total_cases,
        "pass_count": pass_count,
        "routing_pass_rate": pass_rate,
        "precision": pass_rate,
        "recall": pass_rate,
        "results": [result.to_payload() for result in results],
        "confusion_matrix": confusion,
        "neighbor_confusion_count": neighbor_confusion_count,
        "shadow_interference_count": shadow_interference_count,
    }


_ROUTING_TERM_RE = re.compile(r"[\u4e00-\u9fff]{2,}|[a-z0-9][a-z0-9_./+-]*")
_ROUTING_STOPWORDS = {
    "use",
    "when",
    "asked",
    "doing",
    "this",
    "that",
    "with",
    "from",
    "find",
    "test",
    "tests",
    "testing",
    "review",
    "scan",
    "security",
    "漏洞",
    "测试",
    "接口",
    "技能",
    "使用",
    "支持",
}


def _select_description_candidate(*, prompt: str, descriptions: dict[str, str]) -> str | None:
    prompt_text = prompt.casefold()
    ranked = sorted(
        (
            (_score_description(prompt_text, skill_id, description), skill_id)
            for skill_id, description in descriptions.items()
        ),
        key=lambda item: (item[0], item[1]),
        reverse=True,
    )
    if not ranked or ranked[0][0] <= 0:
        return None
    return ranked[0][1]


def _score_description(prompt_text: str, skill_id: str, description: str) -> int:
    score = 0
    terms = _extract_routing_terms(description)
    for term in terms:
        if term in prompt_text:
            score += max(2, min(len(term), 8))
    for skill_token in skill_id.replace("/", " ").replace("-", " ").split():
        normalized = skill_token.casefold()
        if normalized and normalized in prompt_text:
            score += 4
    return score


def _extract_routing_terms(description: str) -> set[str]:
    lowered = description.casefold()
    segments = [
        segment.strip()
        for segment in re.split(r"[，。；、:：()（）\n]+", lowered)
        if segment.strip()
    ]
    tokens = {match.group(0).strip() for match in _ROUTING_TERM_RE.finditer(lowered)}
    return {
        term for term in {*segments, *tokens} if len(term) >= 2 and term not in _ROUTING_STOPWORDS
    }


def evaluate_tasks(
    catalog: GovernedSkillCatalog,
    task_cases_by_skill: dict[str, list[TaskEvalCase]],
    *,
    max_restore_rounds: int = 2,
) -> dict[str, object]:
    skills_by_id = {skill.governance_id: skill for skill in catalog.skills}
    results: list[TaskCaseEvaluationResult] = []
    regression_count = 0
    restore_trigger_count = 0

    for skill_id, cases in task_cases_by_skill.items():
        governed_skill = skills_by_id.get(skill_id)
        if governed_skill is None:
            continue
        reduction = catalog.reductions_by_skill_id.get(skill_id) or reduce_governed_skill(
            governed_skill
        )
        entry_text = Path(governed_skill.entry_file).read_text(encoding="utf-8")
        full_reference_text = "\n\n".join(
            reference.content for reference in governed_skill.references
        )
        for case in cases:
            requires_reference = case.mode == "needs-reference"
            baseline = _evaluate_variant(
                corpus=case.prompt,
                requires_reference=requires_reference,
                format_terms=case.format_terms,
                forbidden_terms=case.forbidden_terms,
                required_terms=case.required_terms,
                selected_references=[],
            )

            original_selected_references = _select_references(governed_skill, case)
            original = _evaluate_variant(
                corpus=f"{entry_text}\n\n{_join_reference_content(original_selected_references)}",
                requires_reference=requires_reference,
                format_terms=case.format_terms,
                forbidden_terms=case.forbidden_terms,
                required_terms=case.required_terms,
                selected_references=[
                    reference.relative_path for reference in original_selected_references
                ],
            )

            reduced_references = _select_references(governed_skill, case)
            reduced = _evaluate_variant(
                corpus=(
                    f"{reduction.reduced_description}\n\n"
                    f"{reduction.reduced_body}\n\n"
                    f"{_join_reference_content(reduced_references)}"
                ),
                requires_reference=requires_reference,
                format_terms=case.format_terms,
                forbidden_terms=case.forbidden_terms,
                required_terms=case.required_terms,
                selected_references=[reference.relative_path for reference in reduced_references],
            )

            restore_rounds = 0
            restored_reduction = reduction
            while (
                not reduced.passed and restore_rounds < max_restore_rounds and reduced.missing_terms
            ):
                restored_reduction = apply_restore_reduction(
                    restored_reduction,
                    source_text=f"{entry_text}\n\n{full_reference_text}",
                    missing_terms=reduced.missing_terms,
                )
                restore_rounds += 1
                reduced = _evaluate_variant(
                    corpus=(
                        f"{restored_reduction.reduced_description}\n\n"
                        f"{restored_reduction.reduced_body}\n\n"
                        f"{_join_reference_content(reduced_references)}"
                    ),
                    requires_reference=requires_reference,
                    format_terms=case.format_terms,
                    forbidden_terms=case.forbidden_terms,
                    required_terms=case.required_terms,
                    selected_references=[
                        reference.relative_path for reference in reduced_references
                    ],
                )

            if restore_rounds > 0:
                restore_trigger_count += 1
            if original.passed and not reduced.passed:
                regression_count += 1

            results.append(
                TaskCaseEvaluationResult(
                    skill_id=skill_id,
                    case_id=case.case_id,
                    mode=case.mode,
                    baseline=baseline,
                    original=original,
                    reduced=reduced,
                    restore_rounds=restore_rounds,
                )
            )

    total_cases = len(results)
    reduced_pass_count = sum(1 for result in results if result.reduced.passed)
    original_pass_count = sum(1 for result in results if result.original.passed)
    return {
        "total_cases": total_cases,
        "baseline_pass_rate": 1.0
        if total_cases == 0
        else sum(1 for result in results if result.baseline.passed) / total_cases,
        "original_pass_rate": 1.0 if total_cases == 0 else original_pass_count / total_cases,
        "reduced_pass_rate": 1.0 if total_cases == 0 else reduced_pass_count / total_cases,
        "regression_count": regression_count,
        "selective_restore_trigger_rate": 0.0
        if total_cases == 0
        else restore_trigger_count / total_cases,
        "coverage": _build_task_coverage_summary(task_cases_by_skill),
        "results": [result.to_payload() for result in results],
    }


def _evaluate_variant(
    *,
    corpus: str,
    requires_reference: bool,
    format_terms: list[str],
    forbidden_terms: list[str],
    required_terms: list[str],
    selected_references: list[str],
) -> TaskVariantResult:
    normalized_corpus = corpus.casefold()
    matched_terms = [term for term in required_terms if term.casefold() in normalized_corpus]
    missing_terms = [term for term in required_terms if term.casefold() not in normalized_corpus]
    missing_format_terms = [
        term for term in format_terms if term.casefold() not in normalized_corpus
    ]
    forbidden_hits = [term for term in forbidden_terms if term.casefold() in normalized_corpus]
    reference_usage_passed = (not requires_reference) or bool(selected_references)

    failure_reasons: list[str] = []
    if missing_terms:
        failure_reasons.append("missing_required_terms")
    if missing_format_terms:
        failure_reasons.append("missing_format_terms")
    if not reference_usage_passed:
        failure_reasons.append("missing_required_reference")
    if forbidden_hits:
        failure_reasons.append("forbidden_terms_present")

    return TaskVariantResult(
        passed=not failure_reasons,
        matched_terms=matched_terms,
        missing_terms=missing_terms + missing_format_terms + forbidden_hits,
        selected_references=selected_references,
        token_cost=estimate_token_count(corpus),
        format_passed=not missing_format_terms,
        reference_usage_passed=reference_usage_passed,
        failure_reasons=failure_reasons,
    )


def _select_references(
    skill: GovernedSkill, case: TaskEvalCase
) -> list[GovernanceReferenceDocument]:
    if case.mode != "needs-reference":
        return []
    requested_topics = {topic.casefold() for topic in case.reference_topics}
    selected = [
        reference
        for reference in skill.references
        if requested_topics & {topic.casefold() for topic in reference.topics}
    ]
    if selected:
        return selected
    return skill.references[:1]


def _join_reference_content(references: list[GovernanceReferenceDocument]) -> str:
    return "\n\n".join(reference.content for reference in references)


def _build_task_coverage_summary(
    task_cases_by_skill: dict[str, list[TaskEvalCase]],
) -> dict[str, object]:
    per_skill: dict[str, dict[str, object]] = {}
    for skill_id, cases in task_cases_by_skill.items():
        mode_counts: dict[str, int] = {}
        for case in cases:
            mode_counts[case.mode] = mode_counts.get(case.mode, 0) + 1
        per_skill[skill_id] = {
            "total_cases": len(cases),
            "mode_counts": mode_counts,
            "meets_recommended_minimum": len(cases) >= 5,
        }
    return {"per_skill": per_skill}


def _coerce_float(value: object) -> float:
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _to_skill_record(
    skill: GovernedSkill,
    *,
    description_override: str | None = None,
) -> SkillRecord:
    parsed = skill.parsed_record
    compat_payload = {
        "source_kind": skill_models.SkillSourceKind.FILESYSTEM.value,
        "activation_paths": list(parsed.activation_paths),
        "dynamic": False,
        "invocable": True,
        "shell_enabled": True,
        "loaded_from": parsed.entry_file,
        "root_label": "repo-skills",
        "discovery_provenance": parsed.discovery_provenance,
        "governance_skill_id": skill.governance_id,
        "governance_family": skill.family,
        "governance_layout": skill.layout.value,
    }
    return SkillRecord(
        id=skill.governance_id,
        source=CompatibilitySource.LOCAL,
        scope=CompatibilityScope.PROJECT,
        root_dir=parsed.root_dir,
        directory_name=parsed.directory_name,
        entry_file=parsed.entry_file,
        name=parsed.name,
        description=description_override or parsed.description,
        compatibility_json=list(parsed.compatibility),
        metadata_json=dict(parsed.metadata),
        parameter_schema_json=dict(parsed.parameter_schema),
        raw_frontmatter_json={**parsed.raw_frontmatter, "_compat": compat_payload},
        status=SkillRecordStatus.LOADED,
        enabled=True,
        error_message=parsed.error_message,
        content_hash=parsed.content_hash,
        last_scanned_at=parsed.last_scanned_at,
    )
