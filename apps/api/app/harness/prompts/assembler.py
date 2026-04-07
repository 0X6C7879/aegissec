from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agent.prompting import build_chat_capability_prompt, build_chat_prompt_budget
from app.agent.token_budget import TokenBudgetAllocation, truncate_text_to_token_budget
from app.db.models import (
    Message,
    MessageKind,
    MessageRole,
    SkillAgentSummaryRead,
    attachments_from_storage,
)
from app.services.chat_runtime import ConversationMessage, sanitize_assistant_content


@dataclass(slots=True)
class HarnessPromptAssembly:
    latest_message_text: str
    available_skills: list[SkillAgentSummaryRead]
    capability_fragments: dict[str, str]
    mcp_tool_inventory: list[dict[str, Any]]
    prompt_budget: TokenBudgetAllocation
    conversation_history: list[Message]
    conversation_messages: list[ConversationMessage]
    history_text: str
    skill_context_prompt: str
    memory_context: Any
    session_state: Any


class HarnessPromptAssembler:
    def __init__(
        self,
        *,
        capability_facade: Any,
        skill_service: Any,
        memory_service: Any,
    ) -> None:
        self._capability_facade = capability_facade
        self._skill_service = skill_service
        self._memory_service = memory_service

    def build(
        self,
        *,
        session: Any,
        repository: Any,
        user_message: Any | None,
        assistant_message: Any,
        branch_id: str | None,
        total_token_budget: int,
        active_hypotheses: list[str] | None = None,
        recent_entities: list[str] | None = None,
        recent_tools: list[str] | None = None,
    ) -> HarnessPromptAssembly:
        latest_message_text = (
            user_message.content if user_message is not None else assistant_message.content
        )
        available_skills = self._skill_service.list_loaded_skills_for_agent(
            session_id=session.id,
            current_prompt=latest_message_text,
            scenario_type="chat_turn",
        )
        capability_fragments = self._capability_facade.build_prompt_fragments(
            session_id=session.id,
            task_name="chat_turn",
            task_description=latest_message_text,
            projection_summary=latest_message_text,
        )
        mcp_tool_inventory = self._capability_facade.build_mcp_tool_inventory()
        prompt_budget = build_chat_prompt_budget(
            total_budget=total_token_budget,
            available_skills=available_skills,
            inventory_summary=capability_fragments["inventory_summary"],
            schema_summary=capability_fragments["schema_summary"],
            prompt_fragment=capability_fragments["prompt_fragment"],
            latest_message_text=latest_message_text,
            history_text="",
        )
        history_token_budget = max(
            prompt_budget.component_tokens.get("history", 0),
            total_token_budget // 2,
        )
        conversation_history = repository.build_conversation_context(
            session_id=session.id,
            branch_id=branch_id,
            rough_token_budget=history_token_budget,
        )
        conversation_messages = self._build_conversation_messages(conversation_history)
        history_text = "\n\n".join(message.content for message in conversation_messages[:-1])
        restored_semantic_state = self._restore_semantic_state(conversation_history)
        resolved_active_hypotheses = (
            active_hypotheses
            if active_hypotheses is not None
            else list(restored_semantic_state.get("active_hypotheses", []))
        )
        resolved_recent_entities = (
            recent_entities
            if recent_entities is not None
            else list(restored_semantic_state.get("recent_entities", []))
        )
        resolved_recent_tools = (
            recent_tools
            if recent_tools is not None
            else list(restored_semantic_state.get("recent_tools", []))
        )
        prompt_budget = build_chat_prompt_budget(
            total_budget=total_token_budget,
            available_skills=available_skills,
            inventory_summary=capability_fragments["inventory_summary"],
            schema_summary=capability_fragments["schema_summary"],
            prompt_fragment=capability_fragments["prompt_fragment"],
            latest_message_text=latest_message_text,
            history_text=history_text,
        )
        memory_context = self._memory_service.build_context(
            session_id=session.id,
            project_id=session.project_id,
            current_task=latest_message_text,
            workflow_phase=session.current_phase,
            session_goal=session.goal,
            scenario_type=session.scenario_type,
            active_hypotheses=resolved_active_hypotheses,
            recent_entities=resolved_recent_entities,
            recent_tools=resolved_recent_tools,
        )
        skill_context_prompt = build_chat_capability_prompt(
            inventory_summary=capability_fragments["inventory_summary"],
            schema_summary=capability_fragments["schema_summary"],
            prompt_fragment=capability_fragments["prompt_fragment"],
            allocated_schema_tokens=prompt_budget.component_tokens.get("capability_schema", 0),
            allocated_prompt_tokens=prompt_budget.component_tokens.get("capability_prompt", 0),
        )
        retrieval_fragment, memory_fragment = self._budget_memory_fragments(
            prompt_budget=prompt_budget,
            retrieval_fragment=memory_context.retrieval_fragment,
            memory_fragment=memory_context.memory_fragment,
        )
        skill_context_prompt = "\n\n".join(
            part
            for part in [skill_context_prompt, retrieval_fragment, memory_fragment]
            if part.strip()
        )
        harness_state_module = __import__("app.harness.state", fromlist=["HarnessSessionState"])
        session_state = harness_state_module.HarnessSessionState(
            session_id=session.id,
            memory_key=memory_context.memory_key,
            current_phase=session.current_phase,
            goal=session.goal,
            scenario_type=session.scenario_type,
            retrieval_manifest=harness_state_module.HarnessRetrievalManifest(
                query_text=latest_message_text,
                memory_key=memory_context.memory_key,
                recalled_entry_ids=[entry.entry_id for entry in memory_context.entries],
                source_labels=[entry.title for entry in memory_context.entries],
                rendered_retrieval_fragment=retrieval_fragment,
                rendered_memory_fragment=memory_fragment,
            ),
            semantic=harness_state_module.HarnessSemanticState(
                active_hypotheses=resolved_active_hypotheses,
                evidence_ids=list(restored_semantic_state.get("evidence_ids", [])),
                graph_hints=[
                    dict(item)
                    for item in restored_semantic_state.get("graph_hints", [])
                    if isinstance(item, dict)
                ],
                artifacts=list(restored_semantic_state.get("artifacts", [])),
                recent_entities=resolved_recent_entities,
                recent_tools=resolved_recent_tools,
                reason=(
                    str(restored_semantic_state.get("reason"))
                    if restored_semantic_state.get("reason") is not None
                    else None
                ),
            ),
        )
        return HarnessPromptAssembly(
            latest_message_text=latest_message_text,
            available_skills=list(available_skills),
            capability_fragments=capability_fragments,
            mcp_tool_inventory=mcp_tool_inventory,
            prompt_budget=prompt_budget,
            conversation_history=conversation_history,
            conversation_messages=conversation_messages,
            history_text=history_text,
            skill_context_prompt=skill_context_prompt,
            memory_context=memory_context,
            session_state=session_state,
        )

    def _budget_memory_fragments(
        self,
        *,
        prompt_budget: TokenBudgetAllocation,
        retrieval_fragment: str,
        memory_fragment: str,
    ) -> tuple[str, str]:
        remaining_budget = prompt_budget.remaining_budget
        if remaining_budget <= 0:
            return "", ""
        retrieval_budget = remaining_budget // 3
        memory_budget = remaining_budget - retrieval_budget
        return (
            truncate_text_to_token_budget(retrieval_fragment, retrieval_budget),
            truncate_text_to_token_budget(memory_fragment, memory_budget),
        )

    def _build_conversation_messages(self, messages: list[Message]) -> list[ConversationMessage]:
        conversation_messages: list[ConversationMessage] = []
        for message in messages:
            if message.message_kind != MessageKind.MESSAGE:
                continue
            if message.role not in {MessageRole.USER, MessageRole.ASSISTANT}:
                continue
            if message.role == MessageRole.ASSISTANT and not message.content.strip():
                continue
            content = message.content
            if message.role == MessageRole.ASSISTANT:
                content = sanitize_assistant_content(message.content)
            conversation_messages.append(
                ConversationMessage(
                    role=message.role,
                    content=content,
                    attachments=attachments_from_storage(message.attachments_json),
                )
            )
        return conversation_messages

    def _restore_semantic_state(self, messages: list[Message]) -> dict[str, Any]:
        for message in reversed(messages):
            metadata = getattr(message, "metadata_json", {})
            if not isinstance(metadata, dict):
                continue
            semantic_state = metadata.get("semantic_state")
            if isinstance(semantic_state, dict):
                return {
                    "active_hypotheses": [
                        str(item)
                        for item in semantic_state.get("active_hypotheses", [])
                        if isinstance(item, str)
                    ],
                    "evidence_ids": [
                        str(item)
                        for item in semantic_state.get("evidence_ids", [])
                        if isinstance(item, str)
                    ],
                    "graph_hints": [
                        dict(item)
                        for item in semantic_state.get("graph_hints", [])
                        if isinstance(item, dict)
                    ],
                    "artifacts": [
                        str(item)
                        for item in semantic_state.get("artifacts", [])
                        if isinstance(item, str)
                    ],
                    "recent_entities": [
                        str(item)
                        for item in semantic_state.get("recent_entities", [])
                        if isinstance(item, str)
                    ],
                    "recent_tools": [
                        str(item)
                        for item in semantic_state.get("recent_tools", [])
                        if isinstance(item, str)
                    ],
                    "reason": semantic_state.get("reason"),
                }
        return {
            "active_hypotheses": [],
            "evidence_ids": [],
            "graph_hints": [],
            "artifacts": [],
            "recent_entities": [],
            "recent_tools": [],
            "reason": None,
        }
