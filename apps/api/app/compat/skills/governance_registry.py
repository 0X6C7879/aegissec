from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
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


def synchronize_registry_entries(
    *,
    entries: list[SkillRegistryEntry],
    skills: list[GovernedSkill],
    default_owner: str = "sec-platform",
    default_version: str = "0.1.0",
) -> list[SkillRegistryEntry]:
    from app.compat.skills.governance_discovery import (
        build_skill_token_summary,
        stable_governance_skill_id,
    )

    existing_by_path = index_registry_by_path(entries)
    existing_by_skill_id = index_registry_by_skill_id(entries)
    consumed_skill_ids: set[str] = set()
    synchronized: list[SkillRegistryEntry] = []
    for skill in sorted(skills, key=lambda item: item.relative_path.casefold()):
        skill_id = stable_governance_skill_id(skill.relative_path)
        token_summary = build_skill_token_summary(skill)
        existing_entry = existing_by_path.get(skill.relative_path.casefold())
        if existing_entry is None:
            existing_entry = existing_by_skill_id.get(skill_id)
        if existing_entry is None:
            inferred_family = _infer_registry_family(
                skill_id=skill_id,
                discovered_family=skill.family,
            )
            synchronized.append(
                SkillRegistryEntry(
                    skill_id=skill_id,
                    path=skill.relative_path,
                    family=inferred_family,
                    owner=default_owner,
                    version=default_version,
                    status=SkillGovernanceStatus.INCUBATING,
                    description_tokens=token_summary["description_tokens"],
                    body_tokens=token_summary["body_tokens"],
                    reference_tokens=token_summary["reference_tokens"],
                )
            )
            continue
        consumed_skill_ids.add(existing_entry.skill_id)
        synchronized.append(
            replace(
                existing_entry,
                path=skill.relative_path,
                family=_infer_registry_family(
                    skill_id=existing_entry.skill_id,
                    discovered_family=skill.family,
                    existing_family=existing_entry.family,
                ),
                description_tokens=token_summary["description_tokens"],
                body_tokens=token_summary["body_tokens"],
                reference_tokens=token_summary["reference_tokens"],
            )
        )

    for entry in entries:
        if entry.skill_id in consumed_skill_ids:
            continue
        synchronized.append(entry)

    return _populate_neighbor_defaults(synchronized)


def refresh_registry_entries(
    *,
    entries: list[SkillRegistryEntry],
    skills: list[GovernedSkill],
    routing_report: dict[str, object] | None = None,
    task_report: dict[str, object] | None = None,
    last_verified_model: str | None = None,
    last_verified_at: str | None = None,
) -> list[SkillRegistryEntry]:
    from app.compat.skills.governance_discovery import build_skill_token_summary

    entries = synchronize_registry_entries(entries=entries, skills=skills)
    skills_by_path = {skill.relative_path.casefold(): skill for skill in skills}
    routing_rates = _extract_per_skill_routing_rates(routing_report)
    task_rates = _extract_per_skill_task_rates(task_report)
    collision_scores = _extract_per_skill_collision_scores(routing_report)
    refreshed: list[SkillRegistryEntry] = []
    verified_at_value = last_verified_at or _timestamp_now_iso()

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
                route_collision_score=collision_scores.get(
                    entry.skill_id, entry.route_collision_score
                ),
                obsolescence_score=_derive_obsolescence_score(
                    invocation_30d=entry.invocation_30d,
                    route_collision_score=collision_scores.get(
                        entry.skill_id, entry.route_collision_score
                    ),
                    routing_pass_rate=routing_rates.get(entry.skill_id, entry.routing_pass_rate),
                    task_pass_rate=task_rates.get(entry.skill_id, entry.task_pass_rate),
                ),
                last_verified_model=last_verified_model or entry.last_verified_model,
                last_verified_at=(
                    verified_at_value if last_verified_model else entry.last_verified_at
                ),
            )
        )
    return refreshed


def write_skill_registry(registry_file: Path, entries: list[SkillRegistryEntry]) -> None:
    registry_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "skills": [entry.to_payload() for entry in entries],
    }
    serialized = yaml.safe_dump(
        payload,
        allow_unicode=True,
        sort_keys=False,
        width=120,
    )
    tmp_file = registry_file.with_suffix(f"{registry_file.suffix}.tmp")
    tmp_file.write_text(serialized, encoding="utf-8")
    tmp_file.replace(registry_file)


def derive_status_proposals(
    *,
    entries: list[SkillRegistryEntry],
    watch_candidates: list[dict[str, object]],
    deprecated_candidates: list[dict[str, object]],
    promote_recovered: bool = False,
    routing_pass_threshold: float = 0.95,
    task_pass_threshold: float = 0.95,
) -> list[dict[str, object]]:
    watch_ids = _index_candidate_reasons(watch_candidates)
    deprecated_ids = _index_candidate_reasons(deprecated_candidates)
    proposals: list[dict[str, object]] = []
    for entry in entries:
        target_status: SkillGovernanceStatus | None = None
        reasons: list[str] = []
        if entry.skill_id in deprecated_ids and entry.status is SkillGovernanceStatus.WATCH:
            target_status = SkillGovernanceStatus.DEPRECATED
            reasons = deprecated_ids[entry.skill_id]
        elif entry.skill_id in watch_ids and entry.status in {
            SkillGovernanceStatus.ACTIVE,
            SkillGovernanceStatus.INCUBATING,
        }:
            target_status = SkillGovernanceStatus.WATCH
            reasons = watch_ids[entry.skill_id]
        elif (
            promote_recovered
            and entry.status is SkillGovernanceStatus.WATCH
            and entry.skill_id not in watch_ids
            and entry.skill_id not in deprecated_ids
            and entry.routing_pass_rate >= routing_pass_threshold
            and entry.task_pass_rate >= task_pass_threshold
        ):
            target_status = SkillGovernanceStatus.ACTIVE
            reasons = ["recovered_above_thresholds"]
        if target_status is None or target_status is entry.status:
            continue
        proposals.append(
            {
                "skill_id": entry.skill_id,
                "from": entry.status.value,
                "to": target_status.value,
                "reasons": reasons,
            }
        )
    return proposals


def apply_status_changes(
    *,
    entries: list[SkillRegistryEntry],
    changes: list[dict[str, object]],
) -> list[SkillRegistryEntry]:
    changes_by_skill: dict[str, SkillGovernanceStatus] = {}
    for change in changes:
        skill_id = change.get("skill_id")
        target = change.get("to")
        if not isinstance(skill_id, str) or not isinstance(target, str):
            raise GovernanceRegistryError("Each status change must include skill_id and to.")
        if target not in SkillGovernanceStatus._value2member_map_:
            raise GovernanceRegistryError(f"Unsupported target status '{target}'.")
        changes_by_skill[skill_id] = SkillGovernanceStatus(target)

    updated_entries: list[SkillRegistryEntry] = []
    for entry in entries:
        target_status = changes_by_skill.get(entry.skill_id)
        if target_status is None:
            updated_entries.append(entry)
            continue
        validate_status_transition(entry.status, target_status)
        updated_entries.append(replace(entry, status=target_status))
    return updated_entries


def validate_status_transition(
    source: SkillGovernanceStatus,
    target: SkillGovernanceStatus,
) -> None:
    allowed: dict[SkillGovernanceStatus, set[SkillGovernanceStatus]] = {
        SkillGovernanceStatus.INCUBATING: {
            SkillGovernanceStatus.ACTIVE,
            SkillGovernanceStatus.WATCH,
            SkillGovernanceStatus.DEPRECATED,
            SkillGovernanceStatus.RETIRED,
        },
        SkillGovernanceStatus.ACTIVE: {
            SkillGovernanceStatus.WATCH,
            SkillGovernanceStatus.DEPRECATED,
            SkillGovernanceStatus.RETIRED,
        },
        SkillGovernanceStatus.WATCH: {
            SkillGovernanceStatus.ACTIVE,
            SkillGovernanceStatus.DEPRECATED,
            SkillGovernanceStatus.RETIRED,
        },
        SkillGovernanceStatus.DEPRECATED: {
            SkillGovernanceStatus.WATCH,
            SkillGovernanceStatus.RETIRED,
        },
        SkillGovernanceStatus.RETIRED: {SkillGovernanceStatus.DEPRECATED},
    }
    if source is target:
        return
    if target not in allowed.get(source, set()):
        raise GovernanceRegistryError(
            f"Invalid status transition: {source.value} -> {target.value}."
        )


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


def _extract_per_skill_collision_scores(report: dict[str, object] | None) -> dict[str, float]:
    if report is None:
        return {}
    reduced = report.get("reduced")
    source = reduced if isinstance(reduced, dict) else report
    raw_results = source.get("results") if isinstance(source, dict) else None
    if not isinstance(raw_results, list):
        return {}
    totals: dict[str, int] = {}
    misses: dict[str, int] = {}
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        skill_id = item.get("expected_skill_id")
        if not isinstance(skill_id, str):
            continue
        totals[skill_id] = totals.get(skill_id, 0) + 1
        if item.get("passed") is not True:
            misses[skill_id] = misses.get(skill_id, 0) + 1
    return {
        skill_id: round(misses.get(skill_id, 0) / total, 4)
        for skill_id, total in totals.items()
        if total > 0
    }


def _derive_obsolescence_score(
    *,
    invocation_30d: int,
    route_collision_score: float,
    routing_pass_rate: float,
    task_pass_rate: float,
) -> float:
    inactivity_component = 1.0 if invocation_30d <= 0 else 0.0
    routing_gap = max(0.0, 1.0 - routing_pass_rate)
    task_gap = max(0.0, 1.0 - task_pass_rate)
    score = (
        0.35 * inactivity_component
        + 0.25 * route_collision_score
        + 0.20 * routing_gap
        + 0.20 * task_gap
    )
    return round(min(max(score, 0.0), 1.0), 4)


def _timestamp_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _populate_neighbor_defaults(entries: list[SkillRegistryEntry]) -> list[SkillRegistryEntry]:
    by_skill_id = {entry.skill_id: entry for entry in entries}
    by_family: dict[str, list[str]] = {}
    by_prefix: dict[str, list[str]] = {}
    for entry in entries:
        if entry.family:
            by_family.setdefault(entry.family, []).append(entry.skill_id)
        by_prefix.setdefault(_skill_neighbor_prefix(entry), []).append(entry.skill_id)

    updated_entries: list[SkillRegistryEntry] = []
    for entry in entries:
        curated_neighbors = [neighbor for neighbor in entry.neighbors if neighbor in by_skill_id]
        if curated_neighbors:
            updated_entries.append(replace(entry, neighbors=curated_neighbors[:5]))
            continue

        neighbor_ids: list[str] = []
        if entry.family:
            neighbor_ids.extend(
                skill_id
                for skill_id in by_family.get(entry.family, [])
                if skill_id != entry.skill_id
            )
        if len(neighbor_ids) < 5:
            neighbor_ids.extend(
                skill_id
                for skill_id in by_prefix.get(_skill_neighbor_prefix(entry), [])
                if skill_id != entry.skill_id and skill_id not in neighbor_ids
            )
        updated_entries.append(replace(entry, neighbors=neighbor_ids[:5]))
    return updated_entries


def _infer_registry_family(
    *,
    skill_id: str,
    discovered_family: str | None,
    existing_family: str | None = None,
) -> str | None:
    if existing_family:
        return existing_family
    if discovered_family:
        return discovered_family
    prefix = skill_id.split("/", maxsplit=1)[0]
    if "-" in prefix:
        return prefix.split("-", maxsplit=1)[0]
    return prefix or None


def _skill_neighbor_prefix(entry: SkillRegistryEntry) -> str:
    if entry.family:
        return entry.family
    return entry.skill_id.split("/", maxsplit=1)[0].split("-", maxsplit=1)[0]


def _index_candidate_reasons(
    candidates: list[dict[str, object]],
) -> dict[str, list[str]]:
    indexed: dict[str, list[str]] = {}
    for candidate in candidates:
        skill_id = candidate.get("skill_id")
        raw_reasons = candidate.get("reasons")
        if not isinstance(skill_id, str):
            continue
        if isinstance(raw_reasons, list):
            reasons = [reason for reason in raw_reasons if isinstance(reason, str)]
        else:
            reasons = []
        indexed[skill_id] = reasons
    return indexed
