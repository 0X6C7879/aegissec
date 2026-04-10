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
    from apps.api.app.compat.skills.governance_discovery import (
        discover_governed_skills,
    )
    from apps.api.app.compat.skills.governance_lint import lint_governed_skills
    from apps.api.app.compat.skills.governance_registry import (
        load_skill_registry,
        synchronize_registry_entries,
    )

    parser = build_argument_parser()
    args = parser.parse_args()
    skills_root = REPO_ROOT / "skills"
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    registry_entries = load_skill_registry(
        REPO_ROOT / "registry" / "skill-registry.yaml"
    )
    discovery = discover_governed_skills(skills_root)
    registry_entries = synchronize_registry_entries(
        entries=registry_entries, skills=discovery.skills
    )
    selected_skills = [
        skill
        for skill in discovery.skills
        if _matches_scope(skill_id=skill.governance_id, family=skill.family, args=args)
    ]
    selected_issues = [
        issue
        for issue in discovery.issues
        if (
            (not args.skill_id and not args.family)
            or any(skill.relative_path == issue.relative_path for skill in selected_skills)
        )
    ]
    issues = lint_governed_skills(
        skills=selected_skills,
        registry_entries=registry_entries,
        discovery_issues=selected_issues,
        strict=args.strict,
    )

    payload = {
        "checked_skill_count": len(selected_skills),
        "issue_count": len(issues),
        "issues": [issue.to_payload() for issue in issues],
    }
    report_path = output_dir / "skill_lint_report.json"
    report_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.dry_run:
        return 0
    has_errors = any(issue.level == "error" for issue in issues)
    return 1 if has_errors else 0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Lint governed skills using file-driven registry metadata."
    )
    parser.add_argument("--skill-id")
    parser.add_argument("--family")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "reports" / "latest"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--all", action="store_true")
    return parser


def _matches_scope(
    *, skill_id: str, family: str | None, args: argparse.Namespace
) -> bool:
    if args.skill_id and skill_id != args.skill_id:
        return False
    if args.family and (family or "") != args.family:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
