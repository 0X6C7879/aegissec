from __future__ import annotations

from dataclasses import dataclass

CORE_LAYER = "core"
ROLE_LAYER = "role"
CAPABILITY_LAYER = "capability"
TASK_LOCAL_LAYER = "task-local"


@dataclass(frozen=True)
class PromptLayerFragment:
    layer: str
    content: str
    cache_key: str
