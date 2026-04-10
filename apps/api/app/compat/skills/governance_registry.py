from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import yaml

from app.compat.skills.governance_models import (
    GovernedSkill,
    RoutingTestCase,
    SkillGovernanceStatus,
    SkillRegistryEntry,
    TaskEvalCase,
)


class GovernanceRegistryError(Exception):
    pass


def load_skill_registry(registry_file: Path) -> list[SkillRegistryEntry]:
    payload = _load_yaml_file(registry_file)
    raw_entries = payload.get("skills") if isinstance(payload, dict) else payload
    if not isinstance(raw_entries, list):
        raise GovernanceRegistryError("Skill registry must contain a top-level 'skills' list.")
    entries: list[SkillRegistryEntry] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise GovernanceRegistryError("Each skill registry entry must be a mapping.")
        status_value = raw_entry.get("status", SkillGovernanceStatus.INCUBATING.value)
        if (
            not isinstance(status_value, str)
            or status_value not in SkillGovernanceStatus._value2member_map_
        ):
            raise GovernanceRegistryError(
                "Skill registry status must be one of incubating/active/watch/deprecated/retired."
            )
        entries.append(
            SkillRegistryEntry(
                skill_id=_require_string(raw_entry, "skill_id"),
                path=_require_string(raw_entry, "path"),
                family=_optional_string(raw_entry.get("family")),
                owner=_require_string(raw_entry, "owner"),
                version=_require_string(raw_entry, "version"),
                status=SkillGovernanceStatus(status_value),
                description_tokens=_optional_int(raw_entry.get("description_tokens")),
                body_tokens=_optional_int(raw_entry.get("body_tokens")),
                reference_tokens=_optional_int(raw_entry.get("reference_tokens")),
                invocation_30d=_optional_int(raw_entry.get("invocation_30d")),
                route_collision_score=_optional_float(raw_entry.get("route_collision_score")),
                task_pass_rate=_optional_float(raw_entry.get("task_pass_rate")),
                routing_pass_rate=_optional_float(raw_entry.get("routing_pass_rate")),
                obsolescence_score=_optional_float(raw_entry.get("obsolescence_score")),
                last_verified_model=_optional_string(raw_entry.get("last_verified_model")),
                last_verified_at=_optional_string(raw_entry.get("last_verified_at")),
                depends_on=_optional_string_list(raw_entry.get("depends_on")),
                neighbors=_optional_string_list(raw_entry.get("neighbors")),
            )
        )
    return entries


def load_routing_testset(routing_testset_file: Path) -> list[RoutingTestCase]:
    payload = _load_yaml_file(routing_testset_file)
    raw_cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(raw_cases, list):
        raise GovernanceRegistryError("Routing testset must contain a top-level 'cases' list.")
    cases: list[RoutingTestCase] = []
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            raise GovernanceRegistryError("Each routing test case must be a mapping.")
        cases.append(
            RoutingTestCase(
                case_id=_require_string(raw_case, "case_id"),
                prompt=_require_string(raw_case, "prompt"),
                expected_skill_id=_require_string(raw_case, "expected_skill_id"),
                touched_paths=_optional_string_list(raw_case.get("touched_paths")),
                available_tools=_optional_string_list(raw_case.get("available_tools")),
                current_prompt=_optional_string(raw_case.get("current_prompt")),
                scenario_type=_optional_string(raw_case.get("scenario_type")),
            )
        )
    return cases


def load_task_eval_cases(task_eval_root: Path) -> dict[str, list[TaskEvalCase]]:
    cases_by_skill: dict[str, list[TaskEvalCase]] = {}
    for task_file in sorted(
        task_eval_root.rglob("*.yaml"), key=lambda item: item.as_posix().casefold()
    ):
        payload = _load_yaml_file(task_file)
        if not isinstance(payload, dict):
            raise GovernanceRegistryError("Task eval fixture files must decode to a mapping.")
        skill_id = _require_string(payload, "skill_id")
        raw_cases = payload.get("cases")
        if not isinstance(raw_cases, list):
            raise GovernanceRegistryError("Task eval fixtures must contain a 'cases' list.")
        if skill_id in cases_by_skill:
            raise GovernanceRegistryError(f"Duplicate task eval fixture for skill_id '{skill_id}'.")
        skill_cases: list[TaskEvalCase] = []
        for raw_case in raw_cases:
            if not isinstance(raw_case, dict):
                raise GovernanceRegistryError("Each task eval case must be a mapping.")
            skill_cases.append(
                TaskEvalCase(
                    case_id=_require_string(raw_case, "case_id"),
                    prompt=_require_string(raw_case, "prompt"),
                    mode=_require_string(raw_case, "mode"),
                    required_terms=_optional_string_list(raw_case.get("required_terms")),
                    reference_topics=_optional_string_list(raw_case.get("reference_topics")),
                    format_terms=_optional_string_list(raw_case.get("format_terms")),
                    forbidden_terms=_optional_string_list(raw_case.get("forbidden_terms")),
                    notes=_optional_string(raw_case.get("notes")),
                )
            )
        cases_by_skill[skill_id] = skill_cases
    return cases_by_skill


def index_registry_by_skill_id(entries: list[SkillRegistryEntry]) -> dict[str, SkillRegistryEntry]:
    return {entry.skill_id: entry for entry in entries}


def index_registry_by_path(entries: list[SkillRegistryEntry]) -> dict[str, SkillRegistryEntry]:
    return {entry.path.casefold(): entry for entry in entries}


def refresh_registry_entries(
    *,
    entries: list[SkillRegistryEntry],
    skills: list[GovernedSkill],
    routing_report: dict[str, object] | None = None,
    task_report: dict[str, object] | None = None,
) -> list[SkillRegistryEntry]:
    from app.compat.skills.governance_discovery import build_skill_token_summary

    skills_by_path = {skill.relative_path.casefold(): skill for skill in skills}
    routing_rates = _extract_per_skill_routing_rates(routing_report)
    task_rates = _extract_per_skill_task_rates(task_report)
    refreshed: list[SkillRegistryEntry] = []

    for entry in entries:
        skill = skills_by_path.get(entry.path.casefold())
        description_tokens = entry.description_tokens
        body_tokens = entry.body_tokens
        reference_tokens = entry.reference_tokens
        if skill is not None:
            token_summary = build_skill_token_summary(skill)
            description_tokens = token_summary["description_tokens"]
            body_tokens = token_summary["body_tokens"]
            reference_tokens = token_summary["reference_tokens"]
        refreshed.append(
            replace(
                entry,
                description_tokens=description_tokens,
                body_tokens=body_tokens,
                reference_tokens=reference_tokens,
                routing_pass_rate=routing_rates.get(entry.skill_id, entry.routing_pass_rate),
                task_pass_rate=task_rates.get(entry.skill_id, entry.task_pass_rate),
            )
        )
    return refreshed


def _load_yaml_file(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise GovernanceRegistryError(f"Invalid YAML in '{path.as_posix()}': {exc}") from exc


def _require_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GovernanceRegistryError(f"Field '{key}' must be a non-empty string.")
    return value.strip()


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _optional_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if not isinstance(value, list):
        raise GovernanceRegistryError("Expected a string list value.")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise GovernanceRegistryError("String lists must only contain non-empty strings.")
        normalized.append(item.strip())
    return normalized


def _optional_int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        raise GovernanceRegistryError("Boolean values are not valid integers here.")
    if isinstance(value, int):
        return value
    raise GovernanceRegistryError("Numeric registry fields must be integers.")


def _optional_float(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, bool):
        raise GovernanceRegistryError("Boolean values are not valid numeric scores.")
    if isinstance(value, int | float):
        return float(value)
    raise GovernanceRegistryError("Score registry fields must be numeric.")


def _extract_per_skill_routing_rates(report: dict[str, object] | None) -> dict[str, float]:
    if report is None:
        return {}
    reduced = report.get("reduced")
    source = reduced if isinstance(reduced, dict) else report
    raw_results = source.get("results") if isinstance(source, dict) else None
    if not isinstance(raw_results, list):
        return {}
    totals: dict[str, int] = {}
    passes: dict[str, int] = {}
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        skill_id = item.get("expected_skill_id")
        if not isinstance(skill_id, str):
            continue
        totals[skill_id] = totals.get(skill_id, 0) + 1
        if item.get("passed") is True:
            passes[skill_id] = passes.get(skill_id, 0) + 1
    return {
        skill_id: passes.get(skill_id, 0) / total for skill_id, total in totals.items() if total > 0
    }


def _extract_per_skill_task_rates(report: dict[str, object] | None) -> dict[str, float]:
    if report is None:
        return {}
    raw_results = report.get("results")
    if not isinstance(raw_results, list):
        return {}
    totals: dict[str, int] = {}
    passes: dict[str, int] = {}
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        skill_id = item.get("skill_id")
        reduced = item.get("reduced")
        if not isinstance(skill_id, str) or not isinstance(reduced, dict):
            continue
        totals[skill_id] = totals.get(skill_id, 0) + 1
        if reduced.get("passed") is True:
            passes[skill_id] = passes.get(skill_id, 0) + 1
    return {
        skill_id: passes.get(skill_id, 0) / total for skill_id, total in totals.items() if total > 0
    }
