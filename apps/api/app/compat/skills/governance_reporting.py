from __future__ import annotations

from statistics import median

from app.compat.skills.governance_config import ROUTING_PASS_THRESHOLD, TASK_PASS_THRESHOLD
from app.compat.skills.governance_discovery import build_skill_token_summary
from app.compat.skills.governance_models import (
    GovernedSkill,
    SkillGovernanceStatus,
    SkillRegistryEntry,
)
from app.compat.skills.governance_reduce import _strip_frontmatter


def build_registry_metrics_report(
    *,
    skills: list[GovernedSkill],
    registry_entries: list[SkillRegistryEntry],
    routing_report: dict[str, object] | None = None,
    task_report: dict[str, object] | None = None,
) -> dict[str, object]:
    status_counts = {status.value: 0 for status in SkillGovernanceStatus}
    for entry in registry_entries:
        status_counts[entry.status.value] = status_counts.get(entry.status.value, 0) + 1

    token_summaries = [build_skill_token_summary(skill) for skill in skills]
    description_values = [summary["description_tokens"] for summary in token_summaries]
    body_values = [summary["body_tokens"] for summary in token_summaries]
    reference_values = [summary["reference_tokens"] for summary in token_summaries]

    route_collision_top = sorted(
        (
            {
                "skill_id": entry.skill_id,
                "route_collision_score": entry.route_collision_score,
            }
            for entry in registry_entries
        ),
        key=lambda item: (item["route_collision_score"], item["skill_id"]),
        reverse=True,
    )[:10]
    obsolescence_top = sorted(
        (
            {
                "skill_id": entry.skill_id,
                "obsolescence_score": entry.obsolescence_score,
            }
            for entry in registry_entries
        ),
        key=lambda item: (item["obsolescence_score"], item["skill_id"]),
        reverse=True,
    )[:10]

    return {
        "skill_total": len(skills),
        "family_total": len({entry.family for entry in registry_entries if entry.family}),
        "status_counts": status_counts,
        "cost_metrics": {
            "description_token_p50": _safe_percentile(description_values),
            "description_token_p95": _safe_p95(description_values),
            "body_token_p50": _safe_percentile(body_values),
            "body_token_p95": _safe_p95(body_values),
            "reference_token_p50": _safe_percentile(reference_values),
            "reference_token_p95": _safe_p95(reference_values),
            "average_injected_tokens": _safe_average(
                [
                    summary["description_tokens"] + summary["body_tokens"]
                    for summary in token_summaries
                ]
            ),
            "average_reference_load_count": _safe_average(_selected_reference_loads(task_report)),
        },
        "quality_metrics": {
            "routing_precision": _numeric_metric(routing_report, "precision"),
            "routing_recall": _numeric_metric(routing_report, "recall"),
            "task_pass_rate": _numeric_metric(task_report, "reduced_pass_rate"),
            "regression_count": int(_numeric_metric(task_report, "regression_count")),
            "selective_restore_trigger_rate": _numeric_metric(
                task_report,
                "selective_restore_trigger_rate",
            ),
        },
        "governance_metrics": {
            "uninvoked_30d_count": sum(
                1 for entry in registry_entries if entry.invocation_30d <= 0
            ),
            "route_collision_top": route_collision_top,
            "obsolescence_top": obsolescence_top,
            "duplicate_content_ratio": _duplicate_content_ratio(skills),
            "watch_candidates": build_watch_candidates(registry_entries),
            "deprecated_candidates": build_deprecated_candidates(registry_entries),
        },
    }


def _safe_percentile(values: list[int]) -> float:
    if not values:
        return 0.0
    return float(median(values))


def _safe_p95(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(int(len(ordered) * 0.95) - 1, 0)
    return float(ordered[index])


def _safe_average(values: list[int]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def _numeric_metric(report: dict[str, object] | None, key: str) -> float:
    if report is None:
        return 0.0
    value = report.get(key)
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def build_watch_candidates(entries: list[SkillRegistryEntry]) -> list[dict[str, object]]:
    return _build_watch_candidates(entries)


def build_deprecated_candidates(entries: list[SkillRegistryEntry]) -> list[dict[str, object]]:
    return _build_deprecated_candidates(entries)


def _duplicate_content_ratio(skills: list[GovernedSkill]) -> float:
    paragraphs: list[str] = []
    for skill in skills:
        paragraphs.extend(_normalized_paragraphs(_strip_frontmatter(skill)))
        for reference in skill.references:
            paragraphs.extend(_normalized_paragraphs(reference.content))
    if not paragraphs:
        return 0.0
    unique = len(set(paragraphs))
    return float((len(paragraphs) - unique) / len(paragraphs))


def _normalized_paragraphs(text: str) -> list[str]:
    normalized: list[str] = []
    for paragraph in text.split("\n\n"):
        collapsed = " ".join(paragraph.split()).casefold()
        stripped_markdown = collapsed.strip("-#*`> ").strip()
        if not stripped_markdown:
            continue
        if len(stripped_markdown) < 12:
            continue
        normalized.append(collapsed)
    return normalized


def _build_watch_candidates(entries: list[SkillRegistryEntry]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for entry in entries:
        if entry.status in {
            SkillGovernanceStatus.INCUBATING,
            SkillGovernanceStatus.RETIRED,
        }:
            continue
        reasons: list[str] = []
        if entry.invocation_30d <= 0:
            reasons.append("unused_in_30d")
        if entry.route_collision_score >= 0.25:
            reasons.append("high_route_collision")
        if entry.task_pass_rate < TASK_PASS_THRESHOLD:
            reasons.append("low_task_pass_rate")
        if entry.routing_pass_rate < ROUTING_PASS_THRESHOLD:
            reasons.append("low_routing_pass_rate")
        if reasons:
            candidates.append({"skill_id": entry.skill_id, "reasons": reasons})
    return candidates


def _build_deprecated_candidates(entries: list[SkillRegistryEntry]) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    for entry in entries:
        reasons: list[str] = []
        if entry.status is SkillGovernanceStatus.WATCH and entry.obsolescence_score >= 0.8:
            reasons.append("watch_and_high_obsolescence")
        if entry.status is SkillGovernanceStatus.WATCH and entry.invocation_30d <= 0:
            reasons.append("watch_and_unused")
        if reasons:
            candidates.append({"skill_id": entry.skill_id, "reasons": reasons})
    return candidates


def _selected_reference_loads(task_report: dict[str, object] | None) -> list[int]:
    if not isinstance(task_report, dict):
        return []
    raw_results = task_report.get("results")
    if not isinstance(raw_results, list):
        return []
    loads: list[int] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        reduced = item.get("reduced")
        if not isinstance(reduced, dict):
            continue
        selected = reduced.get("selected_references")
        if isinstance(selected, list):
            loads.append(len(selected))
    return loads
