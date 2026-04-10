from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


def main() -> int:
    from apps.api.app.compat.skills.governance_config import (
        DEFAULT_THRESHOLDS,
        REFERENCE_LOAD_TARGET,
    )
    from apps.api.app.compat.skills.governance_discovery import discover_governed_skills
    from apps.api.app.compat.skills.governance_registry import (
        GovernanceRegistryError,
        load_skill_registry,
        refresh_registry_entries,
        synchronize_registry_entries,
        write_skill_registry,
    )
    from apps.api.app.compat.skills.governance_reporting import (
        build_registry_metrics_report,
    )

    parser = argparse.ArgumentParser(
        description="Generate deterministic registry metrics from file-driven skill governance."
    )
    parser.add_argument("--skill-id")
    parser.add_argument("--family")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "reports" / "latest"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write-registry", action="store_true")
    parser.add_argument("--last-verified-model")
    parser.add_argument("--last-verified-at")
    parser.add_argument("--strict-thresholds", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    registry_entries = load_skill_registry(
        REPO_ROOT / "registry" / "skill-registry.yaml"
    )
    discovery = discover_governed_skills(REPO_ROOT / "skills")
    synced_registry_entries = synchronize_registry_entries(
        entries=registry_entries,
        skills=discovery.skills,
    )
    routing_report = _load_optional_json(output_dir / "routing_report.json")
    task_report = _load_optional_json(output_dir / "task_report.json")
    if args.strict_thresholds and (routing_report is None or task_report is None):
        raise GovernanceRegistryError(
            "strict threshold checks require routing_report.json "
            "and task_report.json in output_dir."
        )
    if args.write_registry and (args.skill_id or args.family):
        raise GovernanceRegistryError(
            "--write-registry does not support partial syncs; omit --skill-id/--family."
        )
    selected_skills = [
        skill
        for skill in discovery.skills
        if (not args.skill_id or skill.governance_id == args.skill_id)
        and (not args.family or (skill.family or "") == args.family)
    ]
    selected_registry_entries = [
        entry
        for entry in synced_registry_entries
        if (not args.skill_id or entry.skill_id == args.skill_id)
        and (not args.family or (entry.family or "") == args.family)
    ]
    selected_registry_paths = {
        entry.path.casefold() for entry in selected_registry_entries
    }
    selected_skills = [
        skill
        for skill in selected_skills
        if skill.relative_path.casefold() in selected_registry_paths
    ]
    refreshed_registry_entries = refresh_registry_entries(
        entries=selected_registry_entries,
        skills=selected_skills,
        routing_report=routing_report,
        task_report=task_report,
        last_verified_model=args.last_verified_model,
        last_verified_at=args.last_verified_at,
    )
    metrics = build_registry_metrics_report(
        skills=selected_skills,
        registry_entries=refreshed_registry_entries,
        routing_report=routing_report,
        task_report=task_report,
    )
    quality_metrics = metrics.get("quality_metrics")
    cost_metrics = metrics.get("cost_metrics")
    sync_report = {
        "entry_count": len(refreshed_registry_entries),
        "registry_file": str(REPO_ROOT / "registry" / "skill-registry.yaml"),
        "write_requested": args.write_registry,
        "written": False,
        "entries": [entry.to_payload() for entry in refreshed_registry_entries],
    }
    if not args.dry_run:
        (output_dir / "registry_metrics.json").write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if args.write_registry:
            write_skill_registry(
                REPO_ROOT / "registry" / "skill-registry.yaml",
                refreshed_registry_entries,
            )
            sync_report["written"] = True
        (output_dir / "registry_sync_report.json").write_text(
            json.dumps(sync_report, indent=2, ensure_ascii=False, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
    if args.strict_thresholds:
        if not isinstance(quality_metrics, dict) or not isinstance(cost_metrics, dict):
            return 1
        routing_precision = quality_metrics.get("routing_precision")
        task_pass_rate = quality_metrics.get("task_pass_rate")
        reference_load_count = cost_metrics.get("average_reference_load_count")
        if not isinstance(routing_precision, int | float):
            return 1
        if not isinstance(task_pass_rate, int | float):
            return 1
        if not isinstance(reference_load_count, int | float):
            return 1
        if routing_precision < DEFAULT_THRESHOLDS.routing_pass_threshold:
            return 1
        if task_pass_rate < DEFAULT_THRESHOLDS.task_pass_threshold:
            return 1
        if reference_load_count >= REFERENCE_LOAD_TARGET:
            return 1
    return 0


def _load_optional_json(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
