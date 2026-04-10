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
    from apps.api.app.compat.skills.governance_registry import (
        apply_status_changes,
        derive_status_proposals,
        load_skill_registry,
        write_skill_registry,
    )

    parser = argparse.ArgumentParser(
        description="Apply explicit lifecycle status transitions using generated registry metrics."
    )
    parser.add_argument("--skill-id")
    parser.add_argument("--family")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "reports" / "latest"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply-status", action="store_true")
    parser.add_argument("--promote-recovered", action="store_true")
    parser.add_argument("--set-status", action="append", default=[])
    parser.add_argument("--strict-thresholds", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    registry_file = REPO_ROOT / "registry" / "skill-registry.yaml"
    registry_entries = load_skill_registry(registry_file)
    selected_entries = [
        entry
        for entry in registry_entries
        if (not args.skill_id or entry.skill_id == args.skill_id)
        and (not args.family or (entry.family or "") == args.family)
    ]
    metrics_report = _load_optional_json(output_dir / "registry_metrics.json")
    governance_metrics = (
        metrics_report.get("governance_metrics")
        if isinstance(metrics_report, dict)
        else {}
    )
    watch_candidates = (
        governance_metrics.get("watch_candidates")
        if isinstance(governance_metrics, dict)
        else []
    )
    deprecated_candidates = (
        governance_metrics.get("deprecated_candidates")
        if isinstance(governance_metrics, dict)
        else []
    )
    proposals = derive_status_proposals(
        entries=selected_entries,
        watch_candidates=watch_candidates if isinstance(watch_candidates, list) else [],
        deprecated_candidates=deprecated_candidates
        if isinstance(deprecated_candidates, list)
        else [],
        promote_recovered=args.promote_recovered,
        routing_pass_threshold=DEFAULT_THRESHOLDS.routing_pass_threshold,
        task_pass_threshold=DEFAULT_THRESHOLDS.task_pass_threshold,
    )
    manual_changes = [
        _parse_status_override(raw_change) for raw_change in args.set_status
    ]
    all_changes = proposals + manual_changes
    updated_entries = registry_entries
    if args.apply_status and all_changes:
        updated_entries = apply_status_changes(
            entries=registry_entries, changes=all_changes
        )
        if not args.dry_run:
            write_skill_registry(registry_file, updated_entries)

    payload = {
        "registry_file": str(registry_file),
        "selected_skill_count": len(selected_entries),
        "proposal_count": len(proposals),
        "manual_change_count": len(manual_changes),
        "apply_requested": args.apply_status,
        "written": bool(args.apply_status and all_changes and not args.dry_run),
        "changes": all_changes,
    }
    (output_dir / "registry_status_report.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if args.strict_thresholds and not all_changes and not manual_changes:
        return 1
    return 0


def _load_optional_json(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_status_override(value: str) -> dict[str, object]:
    skill_id, separator, target_status = value.partition("=")
    if not separator or not skill_id.strip() or not target_status.strip():
        raise SystemExit("--set-status must use the form skill_id=status")
    return {
        "skill_id": skill_id.strip(),
        "to": target_status.strip(),
        "from": "manual",
        "reasons": ["manual_override"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
