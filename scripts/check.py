from __future__ import annotations

import argparse
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


def _env_truthy(name: str) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return False
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full project checks.")
    parser.set_defaults(strict_governance=_env_truthy("CI"))
    parser.add_argument(
        "--strict-governance",
        dest="strict_governance",
        action="store_true",
        help="Enable strict governance thresholds for routing/task metrics.",
    )
    parser.add_argument(
        "--no-strict-governance",
        dest="strict_governance",
        action="store_false",
        help="Disable strict governance thresholds for routing/task metrics.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    run(["python", str(REPO_ROOT / "scripts" / "sync_requirements.py")], REPO_ROOT)
    run(["uv", "sync", "--all-extras", "--dev"], API_DIR)
    run(["uv", "run", "ruff", "check", "."], API_DIR)
    run(["uv", "run", "black", "--check", "."], API_DIR)
    run(["uv", "run", "mypy", "app", "tests"], API_DIR)
    run(["uv", "run", "pytest"], API_DIR)
    run_api_python([str(REPO_ROOT / "ci" / "lint_skills.py"), "--strict"])
    run_api_python([str(REPO_ROOT / "ci" / "reduce_skill.py")])
    eval_routing_args = [str(REPO_ROOT / "ci" / "eval_routing.py")]
    eval_task_args = [str(REPO_ROOT / "ci" / "eval_task.py")]
    report_metrics_args = [
        str(REPO_ROOT / "ci" / "report_metrics.py"),
        "--write-registry",
        "--last-verified-model",
        "gpt-5.4",
    ]
    if args.strict_governance:
        eval_routing_args.append("--strict-thresholds")
        eval_task_args.append("--strict-thresholds")
        report_metrics_args.insert(1, "--strict-thresholds")
    run_api_python(eval_routing_args)
    run_api_python(eval_task_args)
    run_api_python(report_metrics_args)
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
