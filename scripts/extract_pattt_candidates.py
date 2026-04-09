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

extract_pattt_candidates = import_module(
    "app.services.pattt_context"
).extract_pattt_candidates


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract PATTT candidates from already loaded docs."
    )
    parser.add_argument(
        "--input-json", default=None, help="Inline JSON payload containing loaded_docs."
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        default=None,
        help="Path to a JSON payload containing loaded_docs.",
    )
    return parser


def _load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.input_json:
        return json.loads(str(args.input_json))
    if args.input_file is not None:
        return json.loads(args.input_file.read_text(encoding="utf-8"))
    raw = sys.stdin.read().strip()
    if raw:
        return json.loads(raw)
    raise SystemExit("An input payload containing loaded_docs is required.")


def _coerce_bool(value: object) -> bool:
    return value if isinstance(value, bool) else False


def main() -> int:
    args = build_parser().parse_args()
    payload = _load_payload(args)
    candidates = extract_pattt_candidates(
        loaded_docs=list(payload.get("loaded_docs") or []),
        objective=str(payload.get("objective") or "PATTT extraction"),
        explicit_bypass=_coerce_bool(payload.get("explicit_bypass", False)),
        explicit_exploit=_coerce_bool(payload.get("explicit_exploit", False)),
    )
    print(
        json.dumps(
            [candidate.to_payload() for candidate in candidates],
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
