from __future__ import annotations

from pathlib import Path

from app.core.settings import REPO_ROOT
from app.db.models import CompatibilityScope, CompatibilitySource

from .models import DiscoveredSkillFile, SkillScanRoot


def default_skill_scan_roots(
    *,
    repo_root: Path = REPO_ROOT,
) -> list[SkillScanRoot]:
    return [
        SkillScanRoot(
            source=CompatibilitySource.LOCAL,
            scope=CompatibilityScope.PROJECT,
            root_dir=(repo_root / "skills").resolve().as_posix(),
        ),
    ]


def scan_skill_files(scan_roots: list[SkillScanRoot]) -> list[DiscoveredSkillFile]:
    discovered: dict[str, DiscoveredSkillFile] = {}
    for scan_root in scan_roots:
        root_path = Path(scan_root.root_dir)
        if not root_path.exists() or not root_path.is_dir():
            continue

        for skill_file in root_path.rglob("SKILL.md"):
            if not skill_file.is_file():
                continue

            entry_path = skill_file.resolve().as_posix()
            discovered[entry_path] = DiscoveredSkillFile(
                source=scan_root.source,
                scope=scan_root.scope,
                root_dir=root_path.resolve().as_posix(),
                directory_name=skill_file.parent.name,
                entry_file=entry_path,
            )

    return sorted(
        discovered.values(), key=lambda item: (item.source.value, item.scope.value, item.entry_file)
    )
