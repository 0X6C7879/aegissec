from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.agent.token_budget import (
    TokenBudgetAllocation,
    TokenBudgetComponentRequest,
    allocate_token_budget,
    estimate_token_count,
    truncate_text_to_token_budget,
)
from app.agent.workbench_runtime import (
    project_workspace_rehydrate_from_runtime_payload,
    project_workspace_state_from_runtime_payload,
)
from app.db.models import AttachmentMetadata, MessageRole, SkillAgentSummaryRead


class PromptConversationMessage(Protocol):
    role: MessageRole
    content: str
    attachments: list[AttachmentMetadata]


CORE_IMMUTABLE_PROMPT = "You are assisting an authorized defensive security research workflow."
SAFETY_SCOPE_PROMPT = (
    "Reply in the user's language. Keep answers concise, evidence-oriented, and within the "
    "user's stated scope. The system exposes a dynamic Skills catalog for the current project. "
    "When the user asks which skills are available, asks what a skill does, or asks you to use "
    "a skill, call list_available_skills or read_skill_content before asking generic "
    "clarifying questions, and do not guess skill contents. Skills expose compiled metadata "
    "and may include prepared invocation hints, but they are not callable tool names, so "
    "never emit a tool call using a skill slug such as agent-browser directly. Callable tool "
    "names always include execute_kali_command, list_available_skills, execute_skill, "
    "read_skill_content, create_terminal_session, list_terminal_sessions, "
    "execute_terminal_command, read_terminal_buffer, and stop_terminal_job, and may also "
    "include MCP tool aliases listed in the capability context using the format "
    "mcp__{server}__{tool}. Use execute_skill when you want the server-side facade to "
    "resolve a specific skill without bypassing runtime approval, and use execute_kali_command "
    "only when "
    "shell-based verification or command output would materially improve accuracy. Prefer "
    "batching adjacent low-risk reconnaissance checks into a single command instead of many "
    "small commands, and avoid redundant tool calls once you have enough evidence. After tool "
    "execution, summarize what happened clearly."
)
SYSTEM_PROMPT = f"{CORE_IMMUTABLE_PROMPT} {SAFETY_SCOPE_PROMPT}"
AUTOROUTED_SKILL_PROMPT_HEADER = "## Auto-selected skill:"


@dataclass(frozen=True)
class PromptFragment:
    name: str
    role: str
    content: str
    source: str
    floor_tokens: int = 0
    cache_event_type: str | None = None
    optional: bool = False

    @property
    def token_count(self) -> int:
        return estimate_token_count(self.content)

    def to_provenance(self, allocated_tokens: int | None = None) -> dict[str, object]:
        preview = self.content.strip()
        return {
            "name": self.name,
            "role": self.role,
            "source": self.source,
            "cache_event_type": self.cache_event_type,
            "optional": self.optional,
            "requested_tokens": self.token_count,
            "allocated_tokens": (
                allocated_tokens if allocated_tokens is not None else self.token_count
            ),
            "floor_tokens": self.floor_tokens,
            "preview": preview[:160],
        }


@dataclass(frozen=True)
class PromptAssembly:
    system_prompt: str | None
    messages: list[dict[str, object]]
    fragments: tuple[PromptFragment, ...]
    budget: TokenBudgetAllocation

    def to_provenance(self) -> dict[str, object]:
        return {
            "system_prompt": self.system_prompt,
            "message_count": len(self.messages),
            "fragments": [
                fragment.to_provenance(self.budget.component_tokens.get(fragment.name))
                for fragment in self.fragments
            ],
            "budget": self.budget.to_state(),
        }


def format_message_content(content: str, attachments: list[AttachmentMetadata]) -> str:
    formatted_content = content.strip()
    if not attachments:
        return formatted_content
    attachment_lines: list[str] = []
    for attachment in attachments:
        name = attachment.name or "unnamed"
        content_type = attachment.content_type or "unknown"
        size_bytes = attachment.size_bytes if attachment.size_bytes is not None else "unknown"
        attachment_lines.append(f"- {name} ({content_type}, {size_bytes} bytes)")
    attachment_block = "\n".join(attachment_lines)
    return (
        f"{formatted_content}\n\n"
        "Attachment metadata provided with this message:\n"
        f"{attachment_block}"
    )


def render_skill_catalog_context(available_skills: list[SkillAgentSummaryRead]) -> str | None:
    if not available_skills:
        return None
    primary_skills = [skill for skill in available_skills if skill.role == "primary"]
    supporting_skills = [skill for skill in available_skills if skill.role == "supporting"]
    reference_skills = [skill for skill in available_skills if skill.role == "reference"]
    if not primary_skills and not supporting_skills and not reference_skills:
        primary_skills = list(available_skills)

    lines = ["Primary skill for current context:"]
    if not primary_skills:
        lines.append("- None")
    else:
        for index, skill in enumerate(primary_skills, start=1):
            lines.extend(_render_skill_catalog_entry(skill, index=index))

    lines.append("")
    lines.append("Supporting skills also loaded:")
    if not supporting_skills:
        lines.append("- None")
    else:
        for index, skill in enumerate(supporting_skills, start=1):
            lines.extend(_render_skill_catalog_entry(skill, index=index))

    if reference_skills:
        lines.append("")
        lines.append("Reference-only related skills:")
        for skill in reference_skills:
            lines.extend(_render_skill_catalog_entry(skill, index=None))

    lines.append("")
    lines.append(
        "Use the primary skill by default, and combine supporting skills when subtasks span "
        "planning, execution, validation, or broader general-plus-specific overlap."
    )
    lines.append(
        "If a dispatcher is primary and a specialized skill is supporting, keep both in play: "
        "the dispatcher frames the solve loop while the supporting skill supplies domain detail."
    )
    lines.append(
        "For the real SKILL.md body, call read_skill_content before guessing implementation "
        "details."
    )
    lines.append(
        "If the user asks to list skills, explain a skill, or use a skill, call the skills "
        "tools before asking broad clarification questions. Skill names in this catalog are "
        "reference entries, not callable tools. Fixed callable tool names are "
        "execute_kali_command, list_available_skills, execute_skill, read_skill_content, "
        "create_terminal_session, list_terminal_sessions, execute_terminal_command, "
        "read_terminal_buffer, and stop_terminal_job. Additional "
        "callable MCP tool aliases may appear in the capability context."
    )
    return "\n".join(lines)


def _render_skill_catalog_entry(
    skill: SkillAgentSummaryRead,
    *,
    index: int | None,
) -> list[str]:
    description = " ".join(skill.description.split()) or "No description provided."
    if len(description) > 140:
        description = f"{description[:137].rstrip()}..."
    label = skill.directory_name
    if skill.name != skill.directory_name:
        label = f"{skill.directory_name} (name: {skill.name})"
    prefix = f"- {index}." if index is not None else "-"
    metadata_bits = [f"score={skill.total_score}"]
    if skill.role:
        metadata_bits.append(f"role={skill.role}")
    if skill.agent:
        metadata_bits.append(f"agent={skill.agent}")
    if skill.effort:
        metadata_bits.append(f"effort={skill.effort}")
    family = getattr(skill, "family", None)
    domain = getattr(skill, "domain", None)
    task_mode = getattr(skill, "task_mode", None)
    prepared_for_context = bool(getattr(skill, "prepared_for_context", False))
    prepared_for_execution = bool(getattr(skill, "prepared_for_execution", False))
    if family:
        metadata_bits.append(f"family={family}")
    if domain:
        metadata_bits.append(f"domain={domain}")
    if task_mode:
        metadata_bits.append(f"task_mode={task_mode}")
    if skill.when_to_use:
        metadata_bits.append(f"when_to_use={skill.when_to_use}")
    if skill.paths:
        metadata_bits.append(f"paths={skill.paths}")
    if prepared_for_context:
        metadata_bits.append("prepared_for_context=true")
    if prepared_for_execution:
        metadata_bits.append("prepared_for_execution=true")
    lines = [f"{prefix} {label}: {description} | {' | '.join(metadata_bits)}"]
    if skill.reasons:
        lines.append(f"  why: {'; '.join(skill.reasons[:3])}")
    return lines


def build_chat_capability_prompt(
    *,
    inventory_summary: str,
    schema_summary: str,
    prompt_fragment: str,
    allocated_schema_tokens: int,
    allocated_prompt_tokens: int,
) -> str:
    parts: list[str] = []
    if inventory_summary.strip():
        parts.append(inventory_summary.strip())
    schema_text = truncate_text_to_token_budget(schema_summary, allocated_schema_tokens)
    if schema_text:
        parts.append(schema_text)
    prompt_text = truncate_text_to_token_budget(prompt_fragment, allocated_prompt_tokens)
    if prompt_text:
        parts.append(prompt_text)
    return "\n\n".join(part for part in parts if part)


def split_skill_context_prompt(
    skill_context_prompt: str | None,
) -> tuple[str, str | None]:
    if skill_context_prompt is None:
        return "", None

    normalized_prompt = skill_context_prompt.strip()
    if not normalized_prompt:
        return "", None

    if normalized_prompt.startswith(AUTOROUTED_SKILL_PROMPT_HEADER):
        return "", normalized_prompt

    marker = f"\n\n{AUTOROUTED_SKILL_PROMPT_HEADER}"
    if marker not in normalized_prompt:
        return normalized_prompt, None

    capability_prompt, autorouted_fragment = normalized_prompt.split(marker, maxsplit=1)
    capability_prompt = capability_prompt.strip()
    autorouted_fragment = f"{AUTOROUTED_SKILL_PROMPT_HEADER}{autorouted_fragment}".strip()
    return capability_prompt, autorouted_fragment or None


def build_chat_prompt_budget(
    *,
    total_budget: int,
    available_skills: list[SkillAgentSummaryRead],
    inventory_summary: str,
    schema_summary: str,
    prompt_fragment: str,
    latest_message_text: str,
    history_text: str,
) -> TokenBudgetAllocation:
    skill_catalog_text = render_skill_catalog_context(available_skills) or ""
    return allocate_token_budget(
        total_budget=total_budget,
        components=[
            TokenBudgetComponentRequest(
                name="core_immutable",
                requested_tokens=estimate_token_count(CORE_IMMUTABLE_PROMPT),
                floor_tokens=estimate_token_count(CORE_IMMUTABLE_PROMPT),
                compressible=False,
            ),
            TokenBudgetComponentRequest(
                name="safety_scope",
                requested_tokens=estimate_token_count(SAFETY_SCOPE_PROMPT),
                floor_tokens=estimate_token_count(SAFETY_SCOPE_PROMPT),
                compressible=False,
            ),
            TokenBudgetComponentRequest(
                name="role_prompt",
                requested_tokens=0,
                floor_tokens=0,
                compressible=False,
            ),
            TokenBudgetComponentRequest(
                name="task_local",
                requested_tokens=estimate_token_count(latest_message_text),
                floor_tokens=min(estimate_token_count(latest_message_text), 192),
                compressible=False,
            ),
            TokenBudgetComponentRequest(
                name="retrieval",
                requested_tokens=0,
                floor_tokens=0,
            ),
            TokenBudgetComponentRequest(
                name="history",
                requested_tokens=estimate_token_count(history_text),
                floor_tokens=0,
            ),
            TokenBudgetComponentRequest(
                name="memory",
                requested_tokens=0,
                floor_tokens=0,
            ),
            TokenBudgetComponentRequest(
                name="capability_schema",
                requested_tokens=estimate_token_count(schema_summary),
                floor_tokens=0,
            ),
            TokenBudgetComponentRequest(
                name="capability_prompt",
                requested_tokens=(
                    estimate_token_count(skill_catalog_text)
                    + estimate_token_count(inventory_summary)
                    + estimate_token_count(prompt_fragment)
                ),
                floor_tokens=min(
                    estimate_token_count(skill_catalog_text)
                    + estimate_token_count(inventory_summary)
                    + estimate_token_count(prompt_fragment),
                    192,
                ),
            ),
            TokenBudgetComponentRequest(
                name="task_local_detail",
                requested_tokens=0,
                floor_tokens=0,
            ),
        ],
    )


def _trim_history_messages(
    messages: Sequence[PromptConversationMessage],
    *,
    token_budget: int,
) -> list[PromptConversationMessage]:
    if token_budget <= 0:
        return []
    trimmed: list[PromptConversationMessage] = []
    consumed_tokens = 0
    for message in reversed(messages):
        formatted = format_message_content(message.content, message.attachments)
        message_tokens = estimate_token_count(formatted)
        if trimmed and consumed_tokens + message_tokens > token_budget:
            break
        trimmed.append(message)
        consumed_tokens += message_tokens
    trimmed.reverse()
    return trimmed


def build_openai_prompt_assembly(
    *,
    content: str,
    attachments: list[AttachmentMetadata],
    conversation_messages: Sequence[PromptConversationMessage] | None,
    available_skills: list[SkillAgentSummaryRead],
    skill_context_prompt: str | None,
    total_budget: int,
) -> PromptAssembly:
    skill_catalog_context = render_skill_catalog_context(available_skills)
    latest_message_text = (
        format_message_content(
            conversation_messages[-1].content, conversation_messages[-1].attachments
        )
        if conversation_messages
        else format_message_content(content, attachments)
    )
    history_text = "\n\n".join(
        format_message_content(message.content, message.attachments)
        for message in (conversation_messages[:-1] if conversation_messages else [])
    )
    budget = build_chat_prompt_budget(
        total_budget=total_budget,
        available_skills=available_skills,
        inventory_summary="",
        schema_summary="",
        prompt_fragment=skill_context_prompt or "",
        latest_message_text=latest_message_text,
        history_text=history_text,
    )
    prompt_tokens = budget.component_tokens.get("capability_prompt", 0)
    capability_prompt_source, autorouted_skill_prompt_source = split_skill_context_prompt(
        skill_context_prompt
    )
    capability_prompt_text = truncate_text_to_token_budget(capability_prompt_source, prompt_tokens)
    autorouted_skill_prompt_text = truncate_text_to_token_budget(
        autorouted_skill_prompt_source or "", prompt_tokens
    )
    fragments: list[PromptFragment] = [
        PromptFragment(
            name="core_immutable",
            role="system",
            content=CORE_IMMUTABLE_PROMPT,
            source="shared_prompting",
            floor_tokens=estimate_token_count(CORE_IMMUTABLE_PROMPT),
        ),
        PromptFragment(
            name="safety_scope",
            role="system",
            content=SAFETY_SCOPE_PROMPT,
            source="shared_prompting",
            floor_tokens=estimate_token_count(SAFETY_SCOPE_PROMPT),
        ),
    ]
    messages: list[dict[str, object]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if skill_catalog_context is not None:
        fragments.append(
            PromptFragment(
                name="capability_inventory",
                role="system",
                content=skill_catalog_context,
                source="skills_catalog",
                optional=True,
            )
        )
        messages.append({"role": "system", "content": skill_catalog_context})
    if capability_prompt_text:
        fragments.append(
            PromptFragment(
                name="capability_prompt",
                role="system",
                content=capability_prompt_text,
                source="capability_facade",
            )
        )
    if autorouted_skill_prompt_text:
        fragments.append(
            PromptFragment(
                name="autorouted_skill_context",
                role="system",
                content=autorouted_skill_prompt_text,
                source="autorouted_skill_router",
            )
        )
    combined_prompt_text = "\n\n".join(
        part for part in [capability_prompt_text, autorouted_skill_prompt_text] if part.strip()
    )
    if combined_prompt_text:
        messages.append({"role": "system", "content": combined_prompt_text})
    if conversation_messages:
        history_messages = _trim_history_messages(
            conversation_messages[:-1],
            token_budget=budget.component_tokens.get("history", 0),
        )
        messages.extend(
            {
                "role": message.role.value,
                "content": format_message_content(message.content, message.attachments),
            }
            for message in [*history_messages, conversation_messages[-1]]
        )
    else:
        messages.append({"role": "user", "content": latest_message_text})
    return PromptAssembly(
        system_prompt=None,
        messages=messages,
        fragments=tuple(fragments),
        budget=budget,
    )


def build_anthropic_prompt_assembly(
    *,
    content: str,
    attachments: list[AttachmentMetadata],
    conversation_messages: Sequence[PromptConversationMessage] | None,
    available_skills: list[SkillAgentSummaryRead],
    skill_context_prompt: str | None,
    total_budget: int,
) -> PromptAssembly:
    skill_catalog_context = render_skill_catalog_context(available_skills)
    latest_message_text = (
        format_message_content(
            conversation_messages[-1].content, conversation_messages[-1].attachments
        )
        if conversation_messages
        else format_message_content(content, attachments)
    )
    history_text = "\n\n".join(
        format_message_content(message.content, message.attachments)
        for message in (conversation_messages[:-1] if conversation_messages else [])
    )
    budget = build_chat_prompt_budget(
        total_budget=total_budget,
        available_skills=available_skills,
        inventory_summary="",
        schema_summary="",
        prompt_fragment=skill_context_prompt or "",
        latest_message_text=latest_message_text,
        history_text=history_text,
    )
    prefix_parts: list[str] = []
    if skill_catalog_context is not None:
        prefix_parts.append(skill_catalog_context)
    capability_prompt_source, autorouted_skill_prompt_source = split_skill_context_prompt(
        skill_context_prompt
    )
    capability_prompt_text = truncate_text_to_token_budget(
        capability_prompt_source,
        budget.component_tokens.get("capability_prompt", 0),
    )
    autorouted_skill_prompt_text = truncate_text_to_token_budget(
        autorouted_skill_prompt_source or "",
        budget.component_tokens.get("capability_prompt", 0),
    )
    if capability_prompt_text:
        prefix_parts.append(capability_prompt_text)
    if autorouted_skill_prompt_text:
        prefix_parts.append(autorouted_skill_prompt_text)
    prefix = "\n\n".join(part for part in prefix_parts if part)
    fragments: list[PromptFragment] = [
        PromptFragment(
            name="core_immutable",
            role="system",
            content=CORE_IMMUTABLE_PROMPT,
            source="shared_prompting",
            floor_tokens=estimate_token_count(CORE_IMMUTABLE_PROMPT),
        ),
        PromptFragment(
            name="safety_scope",
            role="system",
            content=SAFETY_SCOPE_PROMPT,
            source="shared_prompting",
            floor_tokens=estimate_token_count(SAFETY_SCOPE_PROMPT),
        ),
        PromptFragment(
            name="capability_prompt",
            role="user_prefix",
            content=prefix,
            source="capability_facade",
            optional=not bool(prefix),
        ),
    ]
    if skill_catalog_context is not None:
        fragments.append(
            PromptFragment(
                name="capability_inventory",
                role="user_prefix",
                content=skill_catalog_context,
                source="skills_catalog",
                optional=True,
            )
        )
    if capability_prompt_text:
        fragments.append(
            PromptFragment(
                name="capability_prompt_detail",
                role="user_prefix",
                content=capability_prompt_text,
                source="capability_facade",
                optional=True,
            )
        )
    if autorouted_skill_prompt_text:
        fragments.append(
            PromptFragment(
                name="autorouted_skill_context",
                role="user_prefix",
                content=autorouted_skill_prompt_text,
                source="autorouted_skill_router",
            )
        )
    messages: list[dict[str, object]] = []
    if conversation_messages:
        history_messages = _trim_history_messages(
            conversation_messages[:-1],
            token_budget=budget.component_tokens.get("history", 0),
        )
        for index, message in enumerate([*history_messages, conversation_messages[-1]]):
            formatted = format_message_content(message.content, message.attachments)
            if index == 0 and prefix:
                formatted = f"{prefix}\n\n{formatted}"
            messages.append({"role": message.role.value, "content": formatted})
    else:
        user_content = latest_message_text if not prefix else f"{prefix}\n\n{latest_message_text}"
        messages.append({"role": MessageRole.USER.value, "content": user_content})
    return PromptAssembly(
        system_prompt=SYSTEM_PROMPT,
        messages=messages,
        fragments=tuple(fragments),
        budget=budget,
    )


def build_workflow_prompting_state(
    *,
    goal: str,
    template_name: str,
    current_stage: str | None,
    task_name: str,
    role_prompt: str,
    sub_agent_role_prompt: str,
    task_description: str,
    retrieval_summary: str,
    history_summary: str,
    memory_summary: str,
    projection_summary: str,
    capability_inventory_summary: str,
    capability_schema_summary: str,
    capability_prompt_fragment: str,
    compact_summary: str = "",
    reinjection_summary: str = "",
    transcript_delta_summary: str = "",
    continuity_metadata: dict[str, object] | None = None,
    total_budget: int = 4096,
) -> dict[str, object]:
    safety_scope_text = (
        f"Workflow goal: {goal}. Template: {template_name}. "
        f"Current stage: {current_stage or 'unknown'}. "
        "Preserve replay/export/session compatibility anchors and stay within the approved scope."
    )
    task_local_text = (
        f"Task: {task_name}. Description: {task_description}. "
        f"Projection summary: {projection_summary}."
    )
    role_text = "\n".join(
        part for part in (role_prompt.strip(), sub_agent_role_prompt.strip()) if part
    )
    compact_reinjection_text = "\n\n".join(
        part
        for part in (
            transcript_delta_summary.strip(),
            compact_summary.strip(),
            reinjection_summary.strip(),
        )
        if part
    )
    normalized_continuity_metadata = (
        {str(key): value for key, value in continuity_metadata.items()}
        if isinstance(continuity_metadata, dict)
        else {}
    )
    raw_reinjected_components = normalized_continuity_metadata.get("reinjected_components")
    raw_recent_delta_ids = normalized_continuity_metadata.get("recent_delta_ids")
    raw_tool_result_delta_ids = normalized_continuity_metadata.get("tool_result_delta_ids")
    reinjected_components = (
        [str(item) for item in raw_reinjected_components if isinstance(item, str)]
        if isinstance(raw_reinjected_components, list)
        else []
    )
    recent_delta_ids = (
        [str(item) for item in raw_recent_delta_ids if isinstance(item, str)]
        if isinstance(raw_recent_delta_ids, list)
        else []
    )
    tool_result_delta_ids = (
        [str(item) for item in raw_tool_result_delta_ids if isinstance(item, str)]
        if isinstance(raw_tool_result_delta_ids, list)
        else []
    )
    fallback_workspace_state = (
        dict(workspace_state)
        if isinstance(
            (workspace_state := normalized_continuity_metadata.get("workspace_state")),
            dict,
        )
        else {}
    )
    fallback_workspace_rehydrate = (
        dict(workspace_rehydrate)
        if isinstance(
            (workspace_rehydrate := normalized_continuity_metadata.get("workspace_rehydrate")),
            dict,
        )
        else {}
    )
    workbench_runtime = (
        dict(workbench_runtime)
        if isinstance(
            (workbench_runtime := normalized_continuity_metadata.get("workbench_runtime")),
            dict,
        )
        else {}
    )
    projected_workspace_state = project_workspace_state_from_runtime_payload(
        workbench_runtime,
        fallback=fallback_workspace_state,
    )
    projected_workspace_rehydrate = project_workspace_rehydrate_from_runtime_payload(
        workbench_runtime,
        fallback=fallback_workspace_rehydrate,
    )
    budget = allocate_token_budget(
        total_budget=total_budget,
        components=[
            TokenBudgetComponentRequest(
                name="core_immutable",
                requested_tokens=estimate_token_count(CORE_IMMUTABLE_PROMPT),
                floor_tokens=estimate_token_count(CORE_IMMUTABLE_PROMPT),
                compressible=False,
            ),
            TokenBudgetComponentRequest(
                name="safety_scope",
                requested_tokens=estimate_token_count(safety_scope_text),
                floor_tokens=min(estimate_token_count(safety_scope_text), 96),
                compressible=False,
            ),
            TokenBudgetComponentRequest(
                name="role_prompt",
                requested_tokens=estimate_token_count(role_text),
                floor_tokens=min(estimate_token_count(role_text), 96),
                compressible=False,
            ),
            TokenBudgetComponentRequest(
                name="task_local",
                requested_tokens=estimate_token_count(task_local_text),
                floor_tokens=min(estimate_token_count(task_local_text), 96),
                compressible=False,
            ),
            TokenBudgetComponentRequest(
                name="retrieval",
                requested_tokens=estimate_token_count(retrieval_summary),
                floor_tokens=0,
            ),
            TokenBudgetComponentRequest(
                name="history",
                requested_tokens=estimate_token_count(history_summary),
                floor_tokens=0,
            ),
            TokenBudgetComponentRequest(
                name="memory",
                requested_tokens=estimate_token_count(memory_summary),
                floor_tokens=0,
            ),
            TokenBudgetComponentRequest(
                name="capability_schema",
                requested_tokens=estimate_token_count(capability_schema_summary),
                floor_tokens=0,
            ),
            TokenBudgetComponentRequest(
                name="capability_prompt",
                requested_tokens=(
                    estimate_token_count(capability_inventory_summary)
                    + estimate_token_count(capability_prompt_fragment)
                ),
                floor_tokens=min(
                    estimate_token_count(capability_inventory_summary)
                    + estimate_token_count(capability_prompt_fragment),
                    96,
                ),
            ),
            TokenBudgetComponentRequest(
                name="task_local_detail",
                requested_tokens=estimate_token_count(projection_summary),
                floor_tokens=0,
            ),
            TokenBudgetComponentRequest(
                name="compact_reinjection",
                requested_tokens=estimate_token_count(compact_reinjection_text),
                floor_tokens=0,
            ),
        ],
    )
    fragments = (
        PromptFragment(
            name="core_immutable",
            role="system",
            content=CORE_IMMUTABLE_PROMPT,
            source="shared_prompting",
            floor_tokens=estimate_token_count(CORE_IMMUTABLE_PROMPT),
        ),
        PromptFragment(
            name="safety_scope",
            role="system",
            content=safety_scope_text,
            source="workflow",
            floor_tokens=min(estimate_token_count(safety_scope_text), 96),
        ),
        PromptFragment(
            name="role_prompt",
            role="system",
            content=role_text,
            source="workflow",
            floor_tokens=min(estimate_token_count(role_text), 96),
        ),
        PromptFragment(
            name="task_local",
            role="user",
            content=task_local_text,
            source="workflow",
            floor_tokens=min(estimate_token_count(task_local_text), 96),
        ),
        PromptFragment(
            name="retrieval",
            role="context",
            content=truncate_text_to_token_budget(
                retrieval_summary, budget.component_tokens.get("retrieval", 0)
            ),
            source="retrieval",
        ),
        PromptFragment(
            name="history",
            role="context",
            content=truncate_text_to_token_budget(
                history_summary, budget.component_tokens.get("history", 0)
            ),
            source="workflow_history",
        ),
        PromptFragment(
            name="memory",
            role="context",
            content=truncate_text_to_token_budget(
                memory_summary, budget.component_tokens.get("memory", 0)
            ),
            source="memory",
        ),
        PromptFragment(
            name="capability_schema",
            role="context",
            content=truncate_text_to_token_budget(
                capability_schema_summary,
                budget.component_tokens.get("capability_schema", 0),
            ),
            source="capability_facade",
            cache_event_type="capability.skills.schema_summary.cache",
        ),
        PromptFragment(
            name="capability_prompt",
            role="context",
            content=build_chat_capability_prompt(
                inventory_summary=capability_inventory_summary,
                schema_summary="",
                prompt_fragment=capability_prompt_fragment,
                allocated_schema_tokens=0,
                allocated_prompt_tokens=budget.component_tokens.get("capability_prompt", 0),
            ),
            source="capability_facade",
            cache_event_type="capability.skills.prompt_fragment.cache",
        ),
        PromptFragment(
            name="task_local_detail",
            role="context",
            content=truncate_text_to_token_budget(
                projection_summary, budget.component_tokens.get("task_local_detail", 0)
            ),
            source="projection",
            optional=True,
        ),
        PromptFragment(
            name="compact_reinjection",
            role="context",
            content=truncate_text_to_token_budget(
                compact_reinjection_text,
                budget.component_tokens.get("compact_reinjection", 0),
            ),
            source="post_compact_reinjection",
            optional=True,
        ),
    )
    return {
        "provider_shape": "workflow",
        "fragments": [
            fragment.to_provenance(budget.component_tokens.get(fragment.name))
            for fragment in fragments
        ],
        "budget": budget.to_state(),
        "continuity": {
            "compact_applied": bool(normalized_continuity_metadata.get("compact_applied", False)),
            "boundary_marker": str(normalized_continuity_metadata.get("boundary_marker") or ""),
            "source": str(normalized_continuity_metadata.get("source") or "workflow"),
            "reinjected_components": reinjected_components,
            "recent_delta_ids": recent_delta_ids,
            "tool_result_delta_ids": tool_result_delta_ids,
            "assistant_turn_carry_forward": str(
                normalized_continuity_metadata.get("assistant_turn_carry_forward") or ""
            ),
            "assistant_turn_next_directive": str(
                normalized_continuity_metadata.get("assistant_turn_next_directive") or ""
            ),
            "assistant_turn_next_hint": str(
                normalized_continuity_metadata.get("assistant_turn_next_hint") or ""
            ),
            "pending_protocol_kind": str(
                normalized_continuity_metadata.get("pending_protocol_kind") or ""
            ),
            "pending_protocol_pause_reason": str(
                normalized_continuity_metadata.get("pending_protocol_pause_reason") or ""
            ),
            "pending_protocol_resume_condition": str(
                normalized_continuity_metadata.get("pending_protocol_resume_condition") or ""
            ),
            "resolved_protocol_kind": str(
                normalized_continuity_metadata.get("resolved_protocol_kind") or ""
            ),
            "resolved_protocol_payload": (
                dict(payload)
                if isinstance(
                    (payload := normalized_continuity_metadata.get("resolved_protocol_payload")),
                    dict,
                )
                else {}
            ),
            "workspace_state": projected_workspace_state,
            "workspace_rehydrate": projected_workspace_rehydrate,
            "workbench_runtime": workbench_runtime,
        },
    }
