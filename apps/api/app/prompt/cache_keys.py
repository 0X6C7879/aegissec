from __future__ import annotations

from dataclasses import dataclass


def _normalize(value: str | None, *, fallback: str) -> str:
    if value is None:
        return fallback
    normalized = "-".join(value.strip().split())
    return normalized.lower() if normalized else fallback


@dataclass(frozen=True)
class PromptFragmentCacheContext:
    session_id: str | None = None
    role: str | None = None
    task_name: str | None = None


def build_fragment_cache_key(
    *,
    layer: str,
    context: PromptFragmentCacheContext,
) -> str:
    normalized_layer = _normalize(layer, fallback="unknown")
    session_key = _normalize(context.session_id, fallback="global")
    role_key = _normalize(context.role, fallback="none")
    task_key = _normalize(context.task_name, fallback="none")
    return (
        f"prompt.fragment.layer:{normalized_layer}"
        f":session:{session_key}:role:{role_key}:task:{task_key}"
    )
