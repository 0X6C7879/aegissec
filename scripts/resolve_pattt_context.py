from __future__ import annotations

import argparse
from importlib import import_module
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

resolve_pattt_context = import_module(
    "app.services.pattt_context"
).resolve_pattt_context


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve README-first PATTT context for a task."
    )
    parser.add_argument(
        "--request-json", default=None, help="Inline JSON request payload."
    )
    parser.add_argument(
        "--request-file",
        type=Path,
        default=None,
        help="Path to a JSON request payload.",
    )
    return parser


def _load_request(args: argparse.Namespace) -> dict[str, Any]:
    if args.request_json:
        return json.loads(str(args.request_json))
    if args.request_file is not None:
        return json.loads(args.request_file.read_text(encoding="utf-8"))
    raw = sys.stdin.read().strip()
    if raw:
        return json.loads(raw)
    raise SystemExit("A PATTT request JSON payload is required.")


def main() -> int:
    args = build_parser().parse_args()
    request = _load_request(args)
    context_pack = resolve_pattt_context(request=request, repo_root=REPO_ROOT)
    print(json.dumps(context_pack.to_payload(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
