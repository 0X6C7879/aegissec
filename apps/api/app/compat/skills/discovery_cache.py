from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:
    from app.db.models import SkillRecord

    from .models import DiscoveredSkillFile, SkillInvocationRequest, SkillScanRoot

_CompiledSkillT = TypeVar("_CompiledSkillT")

_lock = threading.Lock()
_cache: dict[tuple[object, ...], Any] = {}
_last_refresh_ts: float = 0.0


def _is_expired() -> bool:
    from app.core.settings import get_settings

    return time.monotonic() - _last_refresh_ts > get_settings().skill_cache_ttl_seconds


def get_cached(key: tuple[object, ...]) -> Any | None:
    global _last_refresh_ts
    with _lock:
        if _last_refresh_ts > 0.0 and _is_expired():
            _cache.clear()
            _last_refresh_ts = 0.0
            return None
        return _cache.get(key)


def set_cached(key: tuple[object, ...], value: Any) -> None:
    global _last_refresh_ts
    with _lock:
        _cache[key] = value
        _last_refresh_ts = time.monotonic()


def invalidate_cache() -> None:
    global _last_refresh_ts
    with _lock:
        _cache.clear()
        _last_refresh_ts = 0.0


def _invalidate_cache_namespace(namespace: str) -> None:
    global _last_refresh_ts
    with _lock:
        for key in [key for key in _cache if key and key[0] == namespace]:
            _cache.pop(key, None)
        if not _cache:
            _last_refresh_ts = 0.0


def _cache_component(value: object | None) -> str:
    if value is None:
        return ""
    normalized = getattr(value, "value", value)
    return str(normalized)


def canonicalize_skill_path(path_value: str) -> str:
    normalized = path_value.strip()
    if not normalized:
        return normalized
    if "://" in normalized:
        return normalized.rstrip("/")
    return Path(normalized).expanduser().resolve(strict=False).as_posix()


def canonicalize_skill_path_key(path_value: str) -> str:
    return canonicalize_skill_path(path_value).casefold()


def build_root_cache_key(
    *,
    source: object,
    scope: object,
    root_dir: str,
    source_kind: object | None = None,
) -> tuple[str, str, str, str]:
    return (
        _cache_component(source),
        _cache_component(scope),
        _cache_component(source_kind),
        canonicalize_skill_path_key(root_dir),
    )


def build_discovery_provenance(
    *,
    source_root: str,
    entry_file: str,
    relative_path: str,
    source_kind: str,
    root_label: str | None,
    metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    provenance: dict[str, object] = {
        "source_kind": source_kind,
        "configured_root": source_root,
        "canonical_root": canonicalize_skill_path(source_root),
        "entry_file": entry_file,
        "canonical_entry_file": canonicalize_skill_path(entry_file),
        "relative_path": relative_path,
    }
    if root_label is not None:
        provenance["root_label"] = root_label
    if isinstance(metadata, dict):
        origin = metadata.get("origin")
        if isinstance(origin, str) and origin.strip():
            provenance["origin"] = origin.strip()
    return provenance


def build_discovered_file_cache_key(
    discovered_file: DiscoveredSkillFile,
) -> tuple[str, str, str, str, str]:
    return (
        _cache_component(discovered_file.source),
        _cache_component(discovered_file.scope),
        _cache_component(discovered_file.source_kind),
        canonicalize_skill_path_key(discovered_file.root_dir),
        (
            discovered_file.relative_path.casefold()
            if discovered_file.relative_path
            else canonicalize_skill_path_key(discovered_file.entry_file)
        ),
    )


def build_skill_source_identity_key(
    *,
    source_kind: str,
    source_root: str,
    relative_path: str,
    fingerprint: str,
) -> tuple[str, str, str, str]:
    return (
        source_kind,
        canonicalize_skill_path_key(source_root),
        relative_path.casefold(),
        fingerprint,
    )


def invocation_request_signature(invocation_request: SkillInvocationRequest | None) -> str:
    if invocation_request is None:
        return ""
    if (
        not invocation_request.arguments
        and invocation_request.workspace_path is None
        and not invocation_request.touched_paths
        and invocation_request.session_id is None
    ):
        return ""
    payload = {
        "arguments": invocation_request.arguments,
        "workspace_path": invocation_request.workspace_path,
        "touched_paths": invocation_request.touched_paths,
        "session_id": invocation_request.session_id,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(serialized.encode("utf-8")).hexdigest()


def build_compiled_skill_cache_key(
    *,
    record: SkillRecord,
    source_kind: str,
    relative_path: str,
    invocation_request: SkillInvocationRequest | None,
) -> tuple[str, ...]:
    return (
        source_kind,
        canonicalize_skill_path_key(record.root_dir),
        relative_path.casefold(),
        record.id,
        canonicalize_skill_path_key(record.entry_file),
        record.content_hash,
        invocation_request_signature(invocation_request),
    )


@dataclass(slots=True)
class SkillDiscoveryCache:
    def read_entry_content(self, entry_file: str, reader: Callable[[str], str]) -> str:
        canonical_entry = canonicalize_skill_path(entry_file)
        cached = get_cached(("entry_content", canonical_entry))
        if cached is not None:
            return cast(str, cached)
        content = reader(entry_file)
        set_cached(("entry_content", canonical_entry), content)
        return content

    def clear_entry_content_cache(self) -> None:
        _invalidate_cache_namespace("entry_content")

    def clear_scan_roots_cache(self) -> None:
        _invalidate_cache_namespace("scan_roots")

    def get_or_compile_skill(
        self,
        cache_key: tuple[str, ...],
        compiler: Callable[[], _CompiledSkillT],
    ) -> _CompiledSkillT:
        namespaced_key = ("compiled_skill", *cache_key)
        cached = get_cached(namespaced_key)
        if cached is not None:
            return cast(_CompiledSkillT, cached)
        compiled_skill = compiler()
        set_cached(namespaced_key, compiled_skill)
        return compiled_skill

    def get_or_resolve_scan_roots(
        self,
        *,
        include_compatibility_roots: bool,
        extra_dirs: list[str],
        discovery_paths: list[str],
        resolver: Callable[[], list[SkillScanRoot]],
    ) -> list[SkillScanRoot]:
        cache_key = (
            include_compatibility_roots,
            tuple(
                sorted(
                    canonicalize_skill_path(extra_dir)
                    for extra_dir in extra_dirs
                    if extra_dir.strip()
                )
            ),
            tuple(
                sorted(canonicalize_skill_path(path) for path in discovery_paths if path.strip())
            ),
        )
        cached = get_cached(("scan_roots", *cache_key))
        if cached is not None:
            return list(cast(list[Any], cached))
        roots = resolver()
        set_cached(("scan_roots", *cache_key), list(roots))
        return list(roots)


_shared_discovery_cache = SkillDiscoveryCache()


def get_skill_discovery_cache() -> SkillDiscoveryCache:
    return _shared_discovery_cache
