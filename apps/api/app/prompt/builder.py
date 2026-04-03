from __future__ import annotations

from dataclasses import dataclass

from app.prompt.cache_keys import PromptFragmentCacheContext, build_fragment_cache_key
from app.prompt.fragments import (
    CAPABILITY_LAYER,
    CORE_LAYER,
    ROLE_LAYER,
    TASK_LOCAL_LAYER,
    PromptLayerFragment,
)


@dataclass(frozen=True)
class PromptLayerBundle:
    core: PromptLayerFragment
    role: PromptLayerFragment
    capability: PromptLayerFragment
    task_local: PromptLayerFragment


class PromptFragmentBuilder:
    def build_by_role_and_task(
        self,
        *,
        core_text: str,
        role_text: str,
        capability_text: str,
        task_local_text: str,
        session_id: str | None,
        role: str | None,
        task_name: str | None,
    ) -> PromptLayerBundle:
        context = PromptFragmentCacheContext(
            session_id=session_id,
            role=role,
            task_name=task_name,
        )
        return PromptLayerBundle(
            core=PromptLayerFragment(
                layer=CORE_LAYER,
                content=core_text,
                cache_key=build_fragment_cache_key(layer=CORE_LAYER, context=context),
            ),
            role=PromptLayerFragment(
                layer=ROLE_LAYER,
                content=role_text,
                cache_key=build_fragment_cache_key(layer=ROLE_LAYER, context=context),
            ),
            capability=PromptLayerFragment(
                layer=CAPABILITY_LAYER,
                content=capability_text,
                cache_key=build_fragment_cache_key(layer=CAPABILITY_LAYER, context=context),
            ),
            task_local=PromptLayerFragment(
                layer=TASK_LOCAL_LAYER,
                content=task_local_text,
                cache_key=build_fragment_cache_key(layer=TASK_LOCAL_LAYER, context=context),
            ),
        )
