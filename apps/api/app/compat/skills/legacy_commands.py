from __future__ import annotations

from pathlib import Path

from .models import DiscoveredSkillFile, SkillScanRoot, SkillSourceKind


def discover_legacy_command_files(scan_root: SkillScanRoot) -> list[DiscoveredSkillFile]:
    if not scan_root.enabled or scan_root.source_kind != SkillSourceKind.LEGACY_COMMAND_DIRECTORY:
        return []

    root_path = Path(scan_root.root_dir)
    if not root_path.exists() or not root_path.is_dir():
        return []

    discovered: list[DiscoveredSkillFile] = []
    resolved_root = root_path.resolve()
    for command_file in sorted(resolved_root.glob("*.md")):
        if not command_file.is_file():
            continue
        metadata = dict(scan_root.metadata)
        metadata.setdefault("loaded_from", command_file.resolve().as_posix())
        discovered.append(
            DiscoveredSkillFile(
                source=scan_root.source,
                scope=scan_root.scope,
                root_dir=resolved_root.as_posix(),
                directory_name=command_file.stem,
                entry_file=command_file.resolve().as_posix(),
                relative_path=command_file.resolve().relative_to(resolved_root).as_posix(),
                source_kind=SkillSourceKind.LEGACY_COMMAND_DIRECTORY,
                root_label=scan_root.root_label,
                metadata=metadata,
            )
        )
    return discovered
