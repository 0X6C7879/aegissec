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
        evaluate_tasks,
    )
    from apps.api.app.compat.skills.governance_registry import (
        load_skill_registry,
        load_task_eval_cases,
    )

    parser = argparse.ArgumentParser(
        description="Run deterministic task evaluation for governed skills."
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
    task_cases = load_task_eval_cases(REPO_ROOT / "registry" / "task-eval-set")
    if args.skill_id:
        task_cases = {
            skill_id: cases
            for skill_id, cases in task_cases.items()
            if skill_id == args.skill_id
        }
    elif args.family:
        task_cases = {
            skill_id: cases
            for skill_id, cases in task_cases.items()
            if skill_id.startswith(f"{args.family}/") or skill_id == args.family
        }
    report = evaluate_tasks(catalog, task_cases)
    (output_dir / "task_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.dry_run:
        return 0
    reduced_pass_rate = report.get("reduced_pass_rate")
    if not isinstance(reduced_pass_rate, int | float):
        return 1
    return 0 if float(reduced_pass_rate) >= 0.95 else 1


if __name__ == "__main__":
    raise SystemExit(main())
