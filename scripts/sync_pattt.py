from __future__ import annotations

import argparse
from importlib import import_module
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

_pattt_catalog = import_module("app.services.pattt_catalog")
DEFAULT_PATTT_BRANCH = _pattt_catalog.DEFAULT_PATTT_BRANCH
DEFAULT_PATTT_UPSTREAM = _pattt_catalog.DEFAULT_PATTT_UPSTREAM
build_pattt_catalog = _pattt_catalog.build_pattt_catalog
get_pattt_paths = _pattt_catalog.get_pattt_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync PayloadsAllTheThings into knowledge/pattt/repo."
    )
    parser.add_argument(
        "--source-path",
        type=Path,
        default=None,
        help="Optional local PATTT checkout to copy instead of downloading via gh.",
    )
    parser.add_argument(
        "--upstream",
        default=DEFAULT_PATTT_UPSTREAM,
        help="GitHub upstream in owner/repo form.",
    )
    parser.add_argument(
        "--branch",
        default=DEFAULT_PATTT_BRANCH,
        help="Branch or ref name to download when source-path is not supplied.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Skip automatic catalog rebuild after sync.",
    )
    return parser


def _run_gh(*args: str) -> str:
    result = subprocess.run(
        ["gh", *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip() or "gh command failed"
        raise RuntimeError(stderr)
    return result.stdout.strip()


def _copy_tree(source_dir: Path, destination_dir: Path) -> None:
    if destination_dir.exists():
        shutil.rmtree(destination_dir)
    destination_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dir, destination_dir)


def _sync_from_local_checkout(source_path: Path, destination_dir: Path) -> str:
    if not source_path.exists():
        raise FileNotFoundError(f"Local PATTT source path not found: {source_path}")
    _copy_tree(source_path.resolve(), destination_dir)
    return "local-checkout"


def _sync_from_github(*, upstream: str, branch: str, destination_dir: Path) -> str:
    commit_sha = _run_gh("api", f"repos/{upstream}/commits/{branch}", "--jq", ".sha")
    with tempfile.TemporaryDirectory(prefix="pattt-sync-") as temp_dir_string:
        temp_dir = Path(temp_dir_string)
        archive_path = temp_dir / "pattt.zip"
        with archive_path.open("wb") as archive_handle:
            subprocess.run(
                ["gh", "api", f"repos/{upstream}/zipball/{branch}"],
                check=True,
                cwd=REPO_ROOT,
                stdout=archive_handle,
            )
        extract_dir = temp_dir / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive_path, "r") as archive:
            archive.extractall(extract_dir)
        roots = [path for path in extract_dir.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise RuntimeError("Unexpected PATTT archive layout.")
        _copy_tree(roots[0], destination_dir)
    return commit_sha


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    paths = get_pattt_paths(repo_root=REPO_ROOT)

    if args.source_path is not None:
        source_commit = _sync_from_local_checkout(args.source_path, paths.repo_dir)
    else:
        source_commit = _sync_from_github(
            upstream=str(args.upstream),
            branch=str(args.branch),
            destination_dir=paths.repo_dir,
        )

    (paths.repo_dir / ".source-commit").write_text(
        source_commit + "\n", encoding="utf-8"
    )
    paths.cache_dir.mkdir(parents=True, exist_ok=True)

    if not args.skip_build:
        build_pattt_catalog(
            repo_dir=paths.repo_dir,
            catalog_dir=paths.catalog_dir,
            repo_root=REPO_ROOT,
            source_commit=source_commit,
        )

    print(f"Synced PATTT to {paths.repo_dir}")
    print(f"Pinned source commit: {source_commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
