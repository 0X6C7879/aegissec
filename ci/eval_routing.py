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
    from apps.api.app.compat.skills.governance_eval import (
        build_governed_skill_catalog,
        evaluate_routing,
    )
    from apps.api.app.compat.skills.governance_registry import (
        load_routing_testset,
        load_skill_registry,
    )

    parser = argparse.ArgumentParser(
        description="Run deterministic routing evaluation against the governed skill catalog."
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
    catalog = build_governed_skill_catalog(
        REPO_ROOT / "skills", registry_entries=registry_entries
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
    return 0 if float(routing_pass_rate) >= 0.95 else 1


if __name__ == "__main__":
    raise SystemExit(main())
