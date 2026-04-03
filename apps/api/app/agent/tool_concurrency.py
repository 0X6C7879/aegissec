from __future__ import annotations

from dataclasses import dataclass

from app.agent.tool_registry import ToolAccessMode, ToolCapability, ToolSpec
from app.db.models import TaskNode


@dataclass(frozen=True)
class ToolConcurrencySemantics:
    writes_state: bool
    is_concurrency_safe: bool
    is_read_only: bool
    is_destructive: bool
    scheduler_group: str
    access_mode: str
    side_effect_level: str | None
    resource_keys: tuple[str, ...]


def normalize_tool_concurrency(
    *, task: TaskNode, tool_spec: ToolSpec | None
) -> ToolConcurrencySemantics:
    access_mode = _resolve_access_mode(task=task, tool_spec=tool_spec)
    side_effect_level = _resolve_side_effect_level(task=task, tool_spec=tool_spec)
    resource_keys = _resolve_resource_keys(task=task, tool_spec=tool_spec)
    writes_state = _resolve_writes_state(access_mode=access_mode, tool_spec=tool_spec)
    metadata_overrides_allowed = _metadata_overrides_allowed(tool_spec)
    is_read_only = _resolve_bool_override(
        task=task,
        metadata_key="scheduler_is_read_only",
        metadata_allowed=metadata_overrides_allowed,
        fallback=(tool_spec.safety_profile.is_read_only if tool_spec is not None else None),
        default=(access_mode == ToolAccessMode.READ.value and not writes_state),
    )
    is_destructive = _resolve_bool_override(
        task=task,
        metadata_key="scheduler_is_destructive",
        metadata_allowed=metadata_overrides_allowed,
        fallback=(tool_spec.safety_profile.is_destructive if tool_spec is not None else None),
        default=(access_mode == ToolAccessMode.WRITE.value),
    )
    is_concurrency_safe = _resolve_bool_override(
        task=task,
        metadata_key="scheduler_is_concurrency_safe",
        metadata_allowed=metadata_overrides_allowed,
        fallback=(tool_spec.safety_profile.is_concurrency_safe if tool_spec is not None else None),
        default=(is_read_only and not is_destructive),
    )
    normalized_writes_state = not is_read_only
    scheduler_group = (
        "parallel_read_group" if is_read_only and is_concurrency_safe else "serialized_write_group"
    )
    return ToolConcurrencySemantics(
        writes_state=normalized_writes_state,
        is_concurrency_safe=is_concurrency_safe,
        is_read_only=is_read_only,
        is_destructive=is_destructive,
        scheduler_group=scheduler_group,
        access_mode=access_mode,
        side_effect_level=side_effect_level,
        resource_keys=resource_keys,
    )


def _resolve_access_mode(*, task: TaskNode, tool_spec: ToolSpec | None) -> str:
    if tool_spec is not None and tool_spec.access_mode is not None:
        return tool_spec.access_mode.value
    scheduler_access_mode = task.metadata_json.get("scheduler_access_mode")
    if isinstance(scheduler_access_mode, str) and scheduler_access_mode in {
        ToolAccessMode.READ.value,
        ToolAccessMode.WRITE.value,
    }:
        return scheduler_access_mode
    if tool_spec is not None and tool_spec.safety_profile.writes_state:
        return ToolAccessMode.WRITE.value
    return ToolAccessMode.READ.value


def _resolve_side_effect_level(*, task: TaskNode, tool_spec: ToolSpec | None) -> str | None:
    side_effect_level = task.metadata_json.get("scheduler_side_effect_level")
    if isinstance(side_effect_level, str):
        return side_effect_level
    if tool_spec is None:
        return None
    return tool_spec.side_effect_level.value


def _resolve_resource_keys(*, task: TaskNode, tool_spec: ToolSpec | None) -> tuple[str, ...]:
    resource_keys = task.metadata_json.get("scheduler_resource_keys")
    if isinstance(resource_keys, list):
        return tuple(item for item in resource_keys if isinstance(item, str))
    if tool_spec is not None and tool_spec.resource_keys:
        return tool_spec.resource_keys
    return ()


def _resolve_writes_state(*, access_mode: str, tool_spec: ToolSpec | None) -> bool:
    if access_mode == ToolAccessMode.WRITE.value:
        return True
    if tool_spec is None:
        return False
    return tool_spec.safety_profile.writes_state


def _resolve_bool_override(
    *,
    task: TaskNode,
    metadata_key: str,
    metadata_allowed: bool,
    fallback: bool | None,
    default: bool,
) -> bool:
    if isinstance(fallback, bool):
        return fallback
    if metadata_allowed:
        raw_value = task.metadata_json.get(metadata_key)
        if isinstance(raw_value, bool):
            return raw_value
    return default


def _metadata_overrides_allowed(tool_spec: ToolSpec | None) -> bool:
    if tool_spec is None:
        return True
    return tool_spec.capability is ToolCapability.STRUCTURED_RUNTIME
