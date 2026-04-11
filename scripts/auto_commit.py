from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXCLUDES = [
    ".env",
    ".env.local",
    "apps/api/data/*.db",
    "apps/api/data/memory/",
    "apps/api/data/runtime-workspace/",
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


def get_head_commit() -> str:
    result = run_git("rev-parse", "--short", "HEAD", capture_output=True)
    return result.stdout.strip()


def push_with_retry(*args: str, attempts: int = 3, delay_seconds: int = 5) -> None:
    last_error: subprocess.CalledProcessError | None = None

    for attempt in range(1, attempts + 1):
        result = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            capture_output=True,
        )
        if result.stdout:
            print(result.stdout, end="")
        if result.returncode == 0:
            return

        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        last_error = subprocess.CalledProcessError(
            result.returncode,
            ["git", *args],
            output=result.stdout,
            stderr=result.stderr,
        )
        if attempt < attempts:
            print(
                f"Push attempt {attempt} failed. Retrying in {delay_seconds} seconds...",
                file=sys.stderr,
            )
            time.sleep(delay_seconds)

    assert last_error is not None
    raise last_error


def get_status() -> str:
    result = run_git("status", "--short", capture_output=True)
    return result.stdout.strip()


def get_staged_paths() -> list[str]:
    result = run_git("diff", "--cached", "--name-only", "-z", capture_output=True)
    return [path for path in result.stdout.split("\0") if path]


def should_exclude(path: str) -> bool:
    return path in {".env", ".env.local"} or (
        path.startswith("apps/api/data/")
        and (
            path.endswith(".db")
            or path.startswith("apps/api/data/memory/")
            or path.startswith("apps/api/data/runtime-workspace/")
        )
    )


def unstage_excluded_paths() -> None:
    excluded_paths = [path for path in get_staged_paths() if should_exclude(path)]
    if not excluded_paths:
        return

    run_git("restore", "--staged", "--source=HEAD", "--", *excluded_paths)


def get_candidate_paths() -> list[str]:
    result = run_git(
        "ls-files",
        "--modified",
        "--deleted",
        "--others",
        "--exclude-standard",
        "-z",
        capture_output=True,
    )
    return [path for path in result.stdout.split("\0") if path]


def stage_changes_with_excludes() -> None:
    candidate_paths = [
        path for path in get_candidate_paths() if not should_exclude(path)
    ]
    if not candidate_paths:
        return

    run_git("add", "-A", "--", *candidate_paths)


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
    stage_changes_with_excludes()
    unstage_excluded_paths()

    if not get_staged_paths():
        print("No committable changes after exclusions.")
        return 0

    print("Creating commit...")
    run_git("commit", "-m", args.message)

    if not args.push:
        print("Commit created. Skipping push.")
        return 0

    branch = get_current_branch()
    try:
        if has_upstream():
            print("Pushing to tracked upstream...")
            push_with_retry("push")
        else:
            print(f"Pushing branch '{branch}' to origin with upstream tracking...")
            push_with_retry("push", "-u", "origin", branch)
    except subprocess.CalledProcessError:
        print(
            f"Push failed after retries. Local commit {get_head_commit()} was created but not pushed.",
            file=sys.stderr,
        )
        print("Retry with: git push", file=sys.stderr)
        return 1

    print("Push complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
