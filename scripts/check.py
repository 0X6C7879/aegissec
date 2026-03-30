from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_DIR = REPO_ROOT / "apps" / "api"
WEB_DIR = REPO_ROOT / "apps" / "web"
PNPM = ["corepack.cmd", "pnpm"] if os.name == "nt" else ["corepack", "pnpm"]


def run(command: list[str], workdir: Path) -> None:
    subprocess.run(command, cwd=workdir, check=True)


def main() -> int:
    run(["uv", "sync", "--all-extras", "--dev"], API_DIR)
    run(["uv", "run", "ruff", "check", "."], API_DIR)
    run(["uv", "run", "black", "--check", "."], API_DIR)
    run(["uv", "run", "mypy", "app", "tests"], API_DIR)
    run(["uv", "run", "pytest"], API_DIR)

    run([*PNPM, "install"], WEB_DIR)
    run([*PNPM, "lint"], WEB_DIR)
    run([*PNPM, "exec", "tsc", "-b"], WEB_DIR)
    run([*PNPM, "build"], WEB_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
