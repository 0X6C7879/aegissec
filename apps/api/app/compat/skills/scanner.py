from __future__ import annotations

from importlib import import_module
from os import PathLike
from pathlib import Path
from typing import cast

from app.core.settings import REPO_ROOT
from app.db.models import CompatibilityScope, CompatibilitySource

from .models import DiscoveredSkillFile, SkillScanRoot, SkillSourceKind


def default_skill_scan_roots(
    *,
    repo_root: Path = REPO_ROOT,
    home_dir: Path = Path.home(),
    include_compatibility_roots: bool = False,
    extra_dirs: list[str] | None = None,
) -> list[SkillScanRoot]:
    resolved_repo_root = repo_root.resolve()
    roots = [
        SkillScanRoot(
            source=CompatibilitySource.LOCAL,
            scope=CompatibilityScope.PROJECT,
            root_dir=(resolved_repo_root / "skills").as_posix(),
            root_label="repo-skills",
            metadata={"origin": "repo", "active": True},
        ),
        SkillScanRoot(
            source=CompatibilitySource.LOCAL,
            scope=CompatibilityScope.PROJECT,
            root_dir=(resolved_repo_root / "bundled-skills").as_posix(),
            source_kind=SkillSourceKind.BUNDLED,
            root_label="bundled-skills",
            metadata={"origin": "bundled", "active": True},
        ),
        SkillScanRoot(
            source=CompatibilitySource.LOCAL,
            scope=CompatibilityScope.PROJECT,
            root_dir="mcp://skills",
            source_kind=SkillSourceKind.MCP,
            root_label="mcp-skills",
            metadata={"origin": "mcp_registry", "active": True},
        ),
    ]

    if include_compatibility_roots:
        roots.extend(
            [
                SkillScanRoot(
                    source=CompatibilitySource.CLAUDE,
                    scope=CompatibilityScope.PROJECT,
                    root_dir=(resolved_repo_root / ".claude" / "skills").as_posix(),
                    root_label="project-claude-skills",
                    metadata={"origin": "project", "active": True},
                ),
                SkillScanRoot(
                    source=CompatibilitySource.CLAUDE,
                    scope=CompatibilityScope.USER,
                    root_dir=(home_dir.resolve() / ".claude" / "skills").as_posix(),
                    root_label="user-claude-skills",
                    metadata={"origin": "user", "active": True},
                ),
                SkillScanRoot(
                    source=CompatibilitySource.CLAUDE,
                    scope=CompatibilityScope.PROJECT,
                    root_dir=(resolved_repo_root / ".claude" / "commands").as_posix(),
                    source_kind=SkillSourceKind.LEGACY_COMMAND_DIRECTORY,
                    root_label="project-claude-commands",
                    metadata={"origin": "project", "active": True},
                ),
                SkillScanRoot(
                    source=CompatibilitySource.CLAUDE,
                    scope=CompatibilityScope.USER,
                    root_dir=(home_dir.resolve() / ".claude" / "commands").as_posix(),
                    source_kind=SkillSourceKind.LEGACY_COMMAND_DIRECTORY,
                    root_label="user-claude-commands",
                    metadata={"origin": "user", "active": True},
                ),
            ]
        )

    for extra_dir in extra_dirs or []:
        stripped_dir = extra_dir.strip()
        if not stripped_dir:
            continue
        extra_path = Path(stripped_dir).expanduser().resolve()
        roots.append(
            SkillScanRoot(
                source=CompatibilitySource.LOCAL,
                scope=CompatibilityScope.PROJECT,
                root_dir=extra_path.as_posix(),
                root_label="configured-extra-skill-dir",
                metadata={"origin": "configured_extra", "active": True},
            )
        )
    return roots


def compatibility_skill_scan_placeholders(
    *,
    repo_root: Path = REPO_ROOT,
    home_dir: Path = Path.home(),
) -> list[SkillScanRoot]:
    resolved_repo_root = repo_root.resolve()
    resolved_home_dir = home_dir.expanduser().resolve()
    return [
        SkillScanRoot(
            source=CompatibilitySource.CLAUDE,
            scope=CompatibilityScope.PROJECT,
            root_dir=(resolved_repo_root / ".claude" / "skills").as_posix(),
            root_label="project-claude-skills",
            enabled=False,
            metadata={"origin": "project", "placeholder": True},
        ),
        SkillScanRoot(
            source=CompatibilitySource.CLAUDE,
            scope=CompatibilityScope.USER,
            root_dir=(resolved_home_dir / ".claude" / "skills").as_posix(),
            root_label="user-claude-skills",
            enabled=False,
            metadata={"origin": "user", "placeholder": True},
        ),
        SkillScanRoot(
            source=CompatibilitySource.OPENCODE,
            scope=CompatibilityScope.PROJECT,
            root_dir=(resolved_repo_root / ".opencode" / "skills").as_posix(),
            root_label="project-opencode-skills",
            enabled=False,
            metadata={"origin": "project", "placeholder": True},
        ),
        SkillScanRoot(
            source=CompatibilitySource.OPENCODE,
            scope=CompatibilityScope.USER,
            root_dir=(resolved_home_dir / ".config" / "opencode" / "skills").as_posix(),
            root_label="user-opencode-skills",
            enabled=False,
            metadata={"origin": "user", "placeholder": True},
        ),
        SkillScanRoot(
            source=CompatibilitySource.CLAUDE,
            scope=CompatibilityScope.PROJECT,
            root_dir=(resolved_repo_root / ".claude" / "commands").as_posix(),
            source_kind=SkillSourceKind.LEGACY_COMMAND_DIRECTORY,
            root_label="project-claude-commands",
            enabled=False,
            placeholder=True,
            metadata={"origin": "project", "placeholder": True},
        ),
        SkillScanRoot(
            source=CompatibilitySource.CLAUDE,
            scope=CompatibilityScope.USER,
            root_dir=(resolved_home_dir / ".claude" / "commands").as_posix(),
            source_kind=SkillSourceKind.LEGACY_COMMAND_DIRECTORY,
            root_label="user-claude-commands",
            enabled=False,
            placeholder=True,
            metadata={"origin": "user", "placeholder": True},
        ),
        SkillScanRoot(
            source=CompatibilitySource.LOCAL,
            scope=CompatibilityScope.PROJECT,
            root_dir=(resolved_repo_root / "bundled-skills").as_posix(),
            source_kind=SkillSourceKind.BUNDLED,
            root_label="bundled-skills",
            enabled=False,
            placeholder=True,
            metadata={"placeholder": True},
        ),
        SkillScanRoot(
            source=CompatibilitySource.LOCAL,
            scope=CompatibilityScope.PROJECT,
            root_dir="mcp://skills",
            source_kind=SkillSourceKind.MCP,
            root_label="mcp-skills",
            enabled=False,
            placeholder=True,
            metadata={"placeholder": True},
        ),
    ]


def discover_claude_skill_scan_roots(
    candidate_paths: list[str],
) -> list[SkillScanRoot]:
    discovered: dict[str, SkillScanRoot] = {}
    for candidate_path in candidate_paths:
        for base_dir in _iter_search_base_dirs(candidate_path):
            for ancestor in (base_dir, *base_dir.parents):
                skill_root = ancestor / ".claude" / "skills"
                if not skill_root.exists() or not skill_root.is_dir():
                    continue
                resolved_root = skill_root.resolve().as_posix()
                normalized_root = resolved_root.casefold()
                if normalized_root in discovered:
                    continue
                discovered[normalized_root] = SkillScanRoot(
                    source=CompatibilitySource.CLAUDE,
                    scope=CompatibilityScope.PROJECT,
                    root_dir=resolved_root,
                    root_label="dynamic-claude-skills",
                    metadata={"origin": "dynamic_discovery", "active": True},
                )
    return sorted(discovered.values(), key=lambda root: root.root_dir.casefold())


def scan_skill_files(scan_roots: list[SkillScanRoot]) -> list[DiscoveredSkillFile]:
    discovered: dict[str, DiscoveredSkillFile] = {}
    for scan_root in scan_roots:
        if not scan_root.enabled:
            continue
        for discovered_file in _discover_skill_files_for_root(scan_root):
            discovered[discovered_file.entry_file] = discovered_file

    return sorted(
        discovered.values(), key=lambda item: (item.source.value, item.scope.value, item.entry_file)
    )


def _discover_skill_files_for_root(scan_root: SkillScanRoot) -> list[DiscoveredSkillFile]:
    if scan_root.source_kind == SkillSourceKind.BUNDLED:
        bundled_module = import_module("app.compat.skills.bundled")
        return cast(
            list[DiscoveredSkillFile], bundled_module.discover_bundled_skill_files(scan_root)
        )
    if scan_root.source_kind == SkillSourceKind.LEGACY_COMMAND_DIRECTORY:
        legacy_module = import_module("app.compat.skills.legacy_commands")
        return cast(
            list[DiscoveredSkillFile], legacy_module.discover_legacy_command_files(scan_root)
        )
    if scan_root.source_kind != SkillSourceKind.FILESYSTEM:
        return []

    root_path = Path(scan_root.root_dir)
    if not root_path.exists() or not root_path.is_dir():
        return []

    discovered: list[DiscoveredSkillFile] = []
    resolved_root = root_path.resolve()
    for skill_file in resolved_root.glob("*/SKILL.md"):
        if not skill_file.is_file():
            continue

        metadata = dict(scan_root.metadata)
        metadata.setdefault("loaded_from", skill_file.resolve().as_posix())
        discovered.append(
            DiscoveredSkillFile(
                source=scan_root.source,
                scope=scan_root.scope,
                root_dir=resolved_root.as_posix(),
                directory_name=skill_file.parent.name,
                entry_file=skill_file.resolve().as_posix(),
                relative_path=skill_file.resolve().relative_to(resolved_root).as_posix(),
                source_kind=scan_root.source_kind,
                root_label=scan_root.root_label,
                metadata=metadata,
            )
        )
    return discovered


def _iter_search_base_dirs(candidate_path: str | PathLike[str]) -> tuple[Path, ...]:
    candidate = Path(candidate_path).expanduser()
    resolved = candidate.resolve(strict=False)
    if candidate.exists() and candidate.is_file():
        return (resolved.parent,)
    return (resolved,)
