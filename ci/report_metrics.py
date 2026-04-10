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
    from apps.api.app.compat.skills.governance_discovery import discover_governed_skills
    from apps.api.app.compat.skills.governance_registry import (
        load_skill_registry,
        refresh_registry_entries,
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
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    registry_entries = load_skill_registry(
        REPO_ROOT / "registry" / "skill-registry.yaml"
    )
    discovery = discover_governed_skills(REPO_ROOT / "skills")
    routing_report = _load_optional_json(output_dir / "routing_report.json")
    task_report = _load_optional_json(output_dir / "task_report.json")
    selected_skills = [
        skill
        for skill in discovery.skills
        if (not args.skill_id or skill.governance_id == args.skill_id)
        and (not args.family or (skill.family or "") == args.family)
    ]
    selected_registry_entries = [
        entry
        for entry in registry_entries
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
    )
    metrics = build_registry_metrics_report(
        skills=selected_skills,
        registry_entries=refreshed_registry_entries,
        routing_report=routing_report,
        task_report=task_report,
    )
    if not args.dry_run:
        (output_dir / "registry_metrics.json").write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return 0


def _load_optional_json(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
