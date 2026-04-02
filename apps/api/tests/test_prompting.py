from app.agent.prompting import (
    build_chat_capability_prompt,
    build_openai_prompt_assembly,
    build_workflow_prompting_state,
)
from app.db.models import AttachmentMetadata, MessageRole, SkillAgentSummaryRead
from app.services.chat_runtime import ConversationMessage


def test_build_openai_prompt_assembly_preserves_system_layers_and_latest_message() -> None:
    assembly = build_openai_prompt_assembly(
        content="ignored",
        attachments=[],
        conversation_messages=[
            ConversationMessage(role=MessageRole.USER, content="first question"),
            ConversationMessage(role=MessageRole.ASSISTANT, content="first answer"),
            ConversationMessage(
                role=MessageRole.USER,
                content="follow-up",
                attachments=[
                    AttachmentMetadata(name="scope.txt", content_type="text/plain", size_bytes=12)
                ],
            ),
        ],
        available_skills=[
            SkillAgentSummaryRead(
                id="skill-1",
                name="agent-browser",
                directory_name="agent-browser",
                description="Browser automation skill.",
                compatibility=[],
                entry_file="skills/agent-browser/SKILL.md",
            )
        ],
        skill_context_prompt="Loaded skills context.",
        total_budget=12_000,
    )

    assert assembly.messages[0]["role"] == "system"
    assert assembly.messages[1]["role"] == "system"
    assert any(fragment.name == "capability_prompt" for fragment in assembly.fragments)
    assert assembly.messages[-1]["role"] == "user"
    assert "scope.txt" in str(assembly.messages[-1]["content"])


def test_build_chat_capability_prompt_combines_inventory_schema_and_prompt_fragments() -> None:
    result = build_chat_capability_prompt(
        inventory_summary="Inventory",
        schema_summary="Schema",
        prompt_fragment="Prompt",
        allocated_schema_tokens=10,
        allocated_prompt_tokens=10,
    )

    assert "Inventory" in result
    assert "Schema" in result
    assert "Prompt" in result


def test_build_workflow_prompting_state_returns_budget_and_fragment_provenance() -> None:
    prompting = build_workflow_prompting_state(
        goal="Authorized assessment",
        template_name="authorized-assessment",
        current_stage="context_collect",
        task_name="context_collect.attack_surface",
        role_prompt="Collect attack-surface evidence.",
        sub_agent_role_prompt="Stay low-risk and evidence-oriented.",
        task_description="Map exposed services.",
        retrieval_summary="Session retrieval summary.",
        history_summary="Recent execution summary.",
        memory_summary="Working memory summary.",
        projection_summary="Projection level summary.",
        capability_inventory_summary="Inventory summary.",
        capability_schema_summary="Schema summary.",
        capability_prompt_fragment="Prompt fragment.",
    )

    assert prompting["provider_shape"] == "workflow"
    assert isinstance(prompting["fragments"], list)
    assert isinstance(prompting["budget"], dict)
    fragment_names = {fragment["name"] for fragment in prompting["fragments"]}
    assert "core_immutable" in fragment_names
    assert "capability_schema" in fragment_names
