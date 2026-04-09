from __future__ import annotations

import argparse
from importlib import import_module
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

_pattt_catalog = import_module("app.services.pattt_catalog")
get_pattt_paths = _pattt_catalog.get_pattt_paths
validate_pattt_catalog = _pattt_catalog.validate_pattt_catalog


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Validate PATTT catalog coverage against vendored disk state."
    )


def main() -> int:
    build_parser().parse_args()
    paths = get_pattt_paths(repo_root=REPO_ROOT)
    report = validate_pattt_catalog(
        repo_dir=paths.repo_dir,
        catalog_dir=paths.catalog_dir,
        repo_root=REPO_ROOT,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
