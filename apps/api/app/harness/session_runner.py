from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass
from typing import Any

from .messages import ChatRuntime, GenerationCallbacks


@dataclass(slots=True)
class HarnessGenerationPreparation:
    latest_message_text: str
    available_skills: list[Any]
    mcp_tool_inventory: list[dict[str, Any]]
    swarm_coordinator: Any
    prompt_assembly: Any


def chat_runtime_supports_mcp_tools(chat_runtime: ChatRuntime) -> bool:
    try:
        signature = inspect.signature(chat_runtime.generate_reply)
    except (TypeError, ValueError):
        return False
    return "mcp_tools" in signature.parameters


def chat_runtime_supports_harness_state(chat_runtime: ChatRuntime) -> bool:
    try:
        signature = inspect.signature(chat_runtime.generate_reply)
    except (TypeError, ValueError):
        return False
    return "harness_state" in signature.parameters


def build_swarm_coordinator(
    *,
    session: Any,
    chat_runtime: ChatRuntime,
    runtime_service: Any,
    skill_service: Any,
    mcp_service: Any,
    session_id: str,
    prompt_assembly: Any,
    generation_id: str,
    latest_message_text: str,
) -> Any:
    harness_swarm = importlib.import_module("app.harness.swarm")
    build_default_swarm_coordinator = harness_swarm.build_default_swarm_coordinator
    swarm_coordinator = build_default_swarm_coordinator(
        session_id=session_id,
        session_state=prompt_assembly.session_state,
        session=session,
        chat_runtime=chat_runtime,
        runtime_service=runtime_service,
        skill_service=skill_service,
        mcp_service=mcp_service,
    )
    swarm_coordinator.ensure_primary_agent(
        objective=latest_message_text,
        metadata={
            "generation_id": generation_id,
            "phase": prompt_assembly.session_state.current_phase,
        },
    )
    return swarm_coordinator


def build_generate_reply_kwargs(
    *,
    chat_runtime: ChatRuntime,
    prompt_assembly: Any,
    available_skills: list[Any],
    skill_context_prompt: str,
    execute_tool: Any,
    callbacks: GenerationCallbacks,
) -> dict[str, Any]:
    generate_reply_kwargs: dict[str, Any] = {
        "conversation_messages": prompt_assembly.conversation_messages,
        "available_skills": available_skills,
        "skill_context_prompt": skill_context_prompt,
        "execute_tool": execute_tool,
        "callbacks": callbacks,
    }
    if chat_runtime_supports_mcp_tools(chat_runtime):
        generate_reply_kwargs["mcp_tools"] = prompt_assembly.mcp_tool_inventory
    if chat_runtime_supports_harness_state(chat_runtime):
        generate_reply_kwargs["harness_state"] = prompt_assembly.session_state
    return generate_reply_kwargs
