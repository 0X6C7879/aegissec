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


def run_api_python(args: list[str], workdir: Path = REPO_ROOT) -> None:
    run(["uv", "run", "--project", str(API_DIR), "python", *args], workdir)


def main() -> int:
    run(["python", str(REPO_ROOT / "scripts" / "sync_requirements.py")], REPO_ROOT)
    run(["uv", "sync", "--all-extras", "--dev"], API_DIR)
    run(["uv", "run", "ruff", "check", "."], API_DIR)
    run(["uv", "run", "black", "--check", "."], API_DIR)
    run(["uv", "run", "mypy", "app", "tests"], API_DIR)
    run(["uv", "run", "pytest"], API_DIR)
    run_api_python([str(REPO_ROOT / "ci" / "lint_skills.py"), "--strict"])
    run_api_python([str(REPO_ROOT / "ci" / "reduce_skill.py")])
    run_api_python([str(REPO_ROOT / "ci" / "eval_routing.py")])
    run_api_python([str(REPO_ROOT / "ci" / "eval_task.py")])
    run_api_python(
        [
            str(REPO_ROOT / "ci" / "report_metrics.py"),
            "--strict-thresholds",
            "--write-registry",
            "--last-verified-model",
            "gpt-5.4",
        ]
    )
    run(
        ["uv", "run", "python", str(REPO_ROOT / "scripts" / "export_api_schema.py")],
        API_DIR,
    )

    run([*PNPM, "install"], WEB_DIR)
    run([*PNPM, "lint"], WEB_DIR)
    run([*PNPM, "format:check"], WEB_DIR)
    run([*PNPM, "test"], WEB_DIR)
    run([*PNPM, "exec", "tsc", "-b"], WEB_DIR)
    run([*PNPM, "build"], WEB_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
