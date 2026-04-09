from __future__ import annotations

import argparse
from importlib import import_module
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

_pattt_catalog = import_module("app.services.pattt_catalog")
build_pattt_catalog = _pattt_catalog.build_pattt_catalog
get_pattt_paths = _pattt_catalog.get_pattt_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the structured PATTT catalog.")
    parser.add_argument(
        "--source-commit",
        default=None,
        help="Override the source commit recorded in build metadata.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    paths = get_pattt_paths(repo_root=REPO_ROOT)
    result = build_pattt_catalog(
        repo_dir=paths.repo_dir,
        catalog_dir=paths.catalog_dir,
        repo_root=REPO_ROOT,
        source_commit=args.source_commit,
    )
    print(
        "Built PATTT catalog: "
        f"families={len(result['families'])} docs={len(result['docs'])} "
        f"sections={len(result['sections'])} assets={len(result['assets'])}"
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
