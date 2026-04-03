from __future__ import annotations

import re
from pathlib import Path

from app.core.settings import API_ROOT

DEFAULT_MEMORY_ROOT = (API_ROOT / "data" / "memory").resolve()


def ensure_memory_dir(project_id: str, *, base_dir: Path | None = None) -> Path:
    memory_dir = project_memory_dir(project_id, base_dir=base_dir)
    memory_dir.mkdir(parents=True, exist_ok=True)
    entry_dir(project_id, base_dir=base_dir).mkdir(parents=True, exist_ok=True)
    return memory_dir


def memory_root(*, base_dir: Path | None = None) -> Path:
    return (base_dir or DEFAULT_MEMORY_ROOT).resolve()


def project_memory_dir(project_id: str, *, base_dir: Path | None = None) -> Path:
    normalized_project_id = project_id.strip() or "unknown-project"
    return memory_root(base_dir=base_dir) / normalized_project_id


def entry_dir(project_id: str, *, base_dir: Path | None = None) -> Path:
    return project_memory_dir(project_id, base_dir=base_dir) / "entries"


def manifest_path(project_id: str, *, base_dir: Path | None = None) -> Path:
    return project_memory_dir(project_id, base_dir=base_dir) / "MEMORY.md"


def entry_path(project_id: str, entry_id: str, *, base_dir: Path | None = None) -> Path:
    slug = slugify(entry_id)
    return entry_dir(project_id, base_dir=base_dir) / f"{slug}.md"


def slugify(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        return "memory-entry"
    slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return slug or "memory-entry"
