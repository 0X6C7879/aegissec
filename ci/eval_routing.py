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
    from apps.api.app.compat.skills.governance_config import DEFAULT_THRESHOLDS
    from apps.api.app.compat.skills.governance_discovery import discover_governed_skills
    from apps.api.app.compat.skills.governance_eval import (
        build_governed_skill_catalog,
        evaluate_routing,
    )
    from apps.api.app.compat.skills.governance_models import SkillGovernanceStatus
    from apps.api.app.compat.skills.governance_registry import (
        load_routing_testset,
        load_skill_registry,
        synchronize_registry_entries,
    )

    parser = argparse.ArgumentParser(
        description="Run deterministic routing evaluation against the governed skill catalog."
    )
    parser.add_argument("--skill-id")
    parser.add_argument("--family")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "reports" / "latest"))
    parser.add_argument("--dry-run", action="store_true")
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
    selected_registry_entries = [
        entry
        for entry in synced_registry_entries
        if (not args.skill_id or entry.skill_id == args.skill_id)
        and (
            not args.family
            or entry.skill_id.startswith(f"{args.family}/")
            or entry.family == args.family
        )
    ]
    catalog = build_governed_skill_catalog(
        REPO_ROOT / "skills",
        registry_entries=selected_registry_entries,
        allowed_statuses=None
        if args.skill_id or args.family
        else {
            SkillGovernanceStatus.ACTIVE,
            SkillGovernanceStatus.WATCH,
            SkillGovernanceStatus.DEPRECATED,
        },
    )
    cases = [
        case
        for case in load_routing_testset(
            REPO_ROOT / "registry" / "routing-testset.yaml"
        )
        if (not args.skill_id or case.expected_skill_id == args.skill_id)
        and (
            not args.family
            or case.expected_skill_id.startswith(f"{args.family}/")
            or case.expected_skill_id == args.family
        )
    ]
    report = evaluate_routing(catalog, cases)
    (output_dir / "routing_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.dry_run:
        return 0
    routing_pass_rate = report.get("routing_pass_rate")
    if not isinstance(routing_pass_rate, int | float):
        return 1
    threshold = DEFAULT_THRESHOLDS.routing_pass_threshold
    if args.strict_thresholds and float(routing_pass_rate) < threshold:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
