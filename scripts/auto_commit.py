from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXCLUDES = [
    ":(exclude).env",
    ":(exclude).env.local",
    ":(exclude)apps/api/data/*.db",
    ":(exclude)apps/api/data/runtime-workspace/**",
]


def run_git(
    *args: str, capture_output: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def get_status() -> str:
    result = run_git("status", "--short", capture_output=True)
    return result.stdout.strip()


def get_current_branch() -> str:
    result = run_git("branch", "--show-current", capture_output=True)
    branch = result.stdout.strip()
    if not branch:
        raise RuntimeError("Unable to determine current branch")
    return branch


def has_upstream() -> bool:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage, commit, and optionally push repository changes."
    )
    parser.add_argument("message", help="Commit message")
    parser.add_argument("--push", action="store_true", help="Push after commit")
    args = parser.parse_args()

    status = get_status()
    if not status:
        print("No changes to commit.")
        return 0

    print("Staging changes...")
    run_git("add", "-A", "--", ".", *DEFAULT_EXCLUDES)

    print("Creating commit...")
    run_git("commit", "-m", args.message)

    if not args.push:
        print("Commit created. Skipping push.")
        return 0

    branch = get_current_branch()
    if has_upstream():
        print("Pushing to tracked upstream...")
        run_git("push")
    else:
        print(f"Pushing branch '{branch}' to origin with upstream tracking...")
        run_git("push", "-u", "origin", branch)

    print("Push complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
