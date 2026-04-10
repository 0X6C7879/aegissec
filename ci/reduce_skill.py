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
    from apps.api.app.compat.skills.governance_reduce import reduce_governed_skill

    parser = argparse.ArgumentParser(
        description="Create deterministic reduced skill previews."
    )
    parser.add_argument("--skill-id")
    parser.add_argument("--family")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "reports" / "latest"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    discovery = discover_governed_skills(REPO_ROOT / "skills")
    reductions = [
        reduce_governed_skill(skill)
        for skill in discovery.skills
        if (not args.skill_id or skill.governance_id == args.skill_id)
        and (not args.family or (skill.family or "") == args.family)
    ]
    payload = {
        "reduction_count": len(reductions),
        "reductions": [reduction.to_payload() for reduction in reductions],
    }
    (output_dir / "reduction_report.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if args.dry_run or reductions else 1 if args.skill_id else 0


if __name__ == "__main__":
    raise SystemExit(main())
