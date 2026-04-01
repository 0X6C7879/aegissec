from __future__ import annotations

import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_PYPROJECT = REPO_ROOT / "apps" / "api" / "pyproject.toml"
OUTPUT_FILE = REPO_ROOT / "requirements.txt"


def load_dependencies() -> list[str]:
    with API_PYPROJECT.open("rb") as handle:
        project = tomllib.load(handle)

    dependencies = project.get("project", {}).get("dependencies", [])
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) for item in dependencies
    ):
        raise ValueError("apps/api/pyproject.toml project.dependencies is invalid")

    return dependencies


def build_requirements_text(dependencies: list[str]) -> str:
    lines = [
        "# Generated from apps/api/pyproject.toml by scripts/sync_requirements.py",
        "# Run `python scripts/sync_requirements.py` after updating backend dependencies.",
        "",
        *dependencies,
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    dependencies = load_dependencies()
    OUTPUT_FILE.write_text(build_requirements_text(dependencies), encoding="utf-8")
    print(
        f"Wrote {OUTPUT_FILE.relative_to(REPO_ROOT)} with {len(dependencies)} dependencies"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
