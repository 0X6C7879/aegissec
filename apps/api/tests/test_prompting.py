from datetime import datetime
from typing import cast

from app.agent.prompting import (
    build_anthropic_prompt_assembly,
    build_chat_capability_prompt,
    build_openai_prompt_assembly,
    build_workflow_prompting_state,
    render_skill_catalog_context,
    split_skill_context_prompt,
)
from app.compat.mcp.service import MCPService
from app.compat.skills.service import SkillService
from app.db.models import (
    AttachmentMetadata,
    CompatibilityScope,
    CompatibilitySource,
    MCPCapabilityKind,
    MCPCapabilityRead,
    MCPServerRead,
    MCPServerStatus,
    MCPTransport,
    MessageRole,
    SkillAgentSummaryRead,
)
from app.prompt import PromptFragmentBuilder, PromptFragmentCacheContext, build_fragment_cache_key
from app.services.capabilities import CapabilityFacade
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


def test_split_skill_context_prompt_extracts_autorouted_fragment() -> None:
    capability_prompt, autorouted_prompt = split_skill_context_prompt(
        "Capability summary.\n\n## Auto-selected skill: ctf-web\nReason: matched alias"
    )

    assert capability_prompt == "Capability summary."
    assert autorouted_prompt == "## Auto-selected skill: ctf-web\nReason: matched alias"


def test_build_openai_prompt_assembly_tracks_autorouted_skill_as_formal_fragment() -> None:
    assembly = build_openai_prompt_assembly(
        content="分析这个 web ctf 场景",
        attachments=[],
        conversation_messages=None,
        available_skills=[],
        skill_context_prompt=(
            "Capability summary.\n\n"
            "## Auto-selected skill: ctf-web\n"
            "Reason: matched alias tokens 'ctf web'"
        ),
        total_budget=12_000,
    )

    fragment_names = {fragment.name for fragment in assembly.fragments}
    assert "capability_prompt" in fragment_names
    assert "autorouted_skill_context" in fragment_names
    assert any(
        fragment.source == "autorouted_skill_router"
        and "## Auto-selected skill: ctf-web" in fragment.content
        for fragment in assembly.fragments
    )
    system_messages = [message for message in assembly.messages if message["role"] == "system"]
    assert any(
        "## Auto-selected skill: ctf-web" in str(message["content"]) for message in system_messages
    )


def test_build_anthropic_prompt_assembly_tracks_autorouted_skill_as_formal_fragment() -> None:
    assembly = build_anthropic_prompt_assembly(
        content="请处理 docx",
        attachments=[],
        conversation_messages=None,
        available_skills=[],
        skill_context_prompt=(
            "Capability summary.\n\n"
            "## Auto-selected skill: docx\n"
            "Reason: matched explicit skill alias 'docx'"
        ),
        total_budget=12_000,
    )

    assert any(
        fragment.name == "autorouted_skill_context" and fragment.source == "autorouted_skill_router"
        for fragment in assembly.fragments
    )
    assert any(
        "## Auto-selected skill: docx" in str(message["content"]) for message in assembly.messages
    )


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


def test_render_skill_catalog_context_shows_primary_supporting_and_reference_sections() -> None:
    rendered = render_skill_catalog_context(
        [
            SkillAgentSummaryRead(
                id="skill-1",
                name="api-skill",
                directory_name="api-skill",
                description="API validation skill.",
                compatibility=[],
                entry_file="skills/api-skill/SKILL.md",
                total_score=42,
                role="primary",
                reasons=["path matched"],
            ),
            SkillAgentSummaryRead(
                id="skill-2",
                name="triage-planner",
                directory_name="triage-planner",
                description="General triage skill.",
                compatibility=[],
                entry_file="skills/triage-planner/SKILL.md",
                total_score=18,
                role="supporting",
                reasons=["general + specialized pairing"],
            ),
            SkillAgentSummaryRead(
                id="skill-3",
                name="mcp-ref",
                directory_name="mcp-ref",
                description="Reference skill.",
                compatibility=[],
                entry_file="skills/mcp-ref/SKILL.md",
                total_score=9,
                role="reference",
                reasons=["non-executable context"],
            ),
        ]
    )

    assert rendered is not None
    assert "Primary skill for current context" in rendered
    assert "Supporting skills also loaded" in rendered
    assert "Reference-only related skills" in rendered


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
        compact_summary="Compact summary.",
        reinjection_summary="Reinjection summary.",
        continuity_metadata={
            "compact_applied": True,
            "boundary_marker": "compact-boundary:1",
            "source": "post_compact_reinjection",
            "reinjected_components": ["retrieval_summary", "session_memory_summary"],
        },
    )

    assert prompting["provider_shape"] == "workflow"
    assert isinstance(prompting["fragments"], list)
    assert isinstance(prompting["budget"], dict)
    assert isinstance(prompting["continuity"], dict)
    fragment_names = {fragment["name"] for fragment in prompting["fragments"]}
    assert "core_immutable" in fragment_names
    assert "capability_schema" in fragment_names
    assert "compact_reinjection" in fragment_names
    continuity = cast(dict[str, object], prompting["continuity"])
    assert continuity["compact_applied"] is True
    assert continuity["boundary_marker"] == "compact-boundary:1"


def test_prompt_fragment_cache_keys_are_layer_specific() -> None:
    context = PromptFragmentCacheContext(
        session_id="session-1",
        role="Analyst",
        task_name="context_collect.attack_surface",
    )

    core_key = build_fragment_cache_key(layer="core", context=context)
    role_key = build_fragment_cache_key(layer="role", context=context)
    capability_key = build_fragment_cache_key(layer="capability", context=context)
    task_local_key = build_fragment_cache_key(layer="task-local", context=context)

    assert core_key != role_key
    assert role_key != capability_key
    assert capability_key != task_local_key


def test_prompt_fragment_builder_builds_core_role_capability_and_task_local_layers() -> None:
    builder = PromptFragmentBuilder()
    bundle = builder.build_by_role_and_task(
        core_text="core",
        role_text="role",
        capability_text="capability",
        task_local_text="task-local",
        session_id="session-1",
        role="analyst",
        task_name="task-1",
    )

    assert bundle.core.layer == "core"
    assert bundle.role.layer == "role"
    assert bundle.capability.layer == "capability"
    assert bundle.task_local.layer == "task-local"
    assert bundle.capability.content == "capability"
    assert "layer:capability" in bundle.capability.cache_key


def test_capability_prompt_fragment_builder_coexists_with_existing_prompt_behavior() -> None:
    class _SkillServiceStub:
        def build_active_skill_snapshot(self, **_: object) -> list[dict[str, object]]:
            return []

        def build_skill_context_payload(self, **_: object) -> dict[str, object]:
            return {
                "skills": [
                    {
                        "directory_name": "agent-browser",
                        "name": "agent-browser",
                        "description": "Browser automation skill",
                        "invocable": True,
                        "aliases": ["browser"],
                        "allowed_tools": ["execute_skill"],
                        "argument_hint": "--url <value>",
                        "shell_enabled": True,
                        "prepared_invocation": {
                            "shell_expansion_count": 0,
                            "pending_action_count": 0,
                        },
                        "parameter_schema": {"type": "object"},
                    }
                ]
            }

    class _MCPServiceStub:
        def list_servers(self) -> list[object]:
            return []

    facade = CapabilityFacade(
        skill_service=cast(SkillService, _SkillServiceStub()),
        mcp_service=cast(MCPService, _MCPServiceStub()),
    )
    fragment = facade.build_skill_prompt_fragment(
        session_id="session-1",
        role_prompt="Collect evidence",
        task_name="context_collect.attack_surface",
    )

    assert "Loaded skills inventory:" in fragment
    assert (
        "ranked compiled metadata, selection rationale, and prepared invocation hints" in fragment
    )
    assert "execute_skill" in fragment


def test_capability_facade_build_skill_snapshot_uses_compiled_skill_metadata() -> None:
    class _SkillServiceStub:
        def build_active_skill_snapshot(self, **_: object) -> list[dict[str, object]]:
            return [
                {
                    "id": "skill-1",
                    "directory_name": "agent-browser",
                    "name": "agent-browser",
                    "description": "Browser automation skill",
                    "source": "local",
                    "scope": "project",
                    "source_kind": "filesystem",
                    "invocable": True,
                    "user_invocable": True,
                    "conditional": False,
                    "paths": [],
                    "aliases": ["browser"],
                    "allowed_tools": ["execute_skill"],
                    "argument_hint": "--url <value>",
                    "shell_enabled": True,
                    "execution_mode": "reference_only",
                    "resolved_identity": {"relative_path": "agent-browser/SKILL.md"},
                    "prepared_invocation": {
                        "request": {"session_id": "session-1"},
                        "context": {"skill_directory": "skills/agent-browser"},
                        "shell_expansion_count": 0,
                        "pending_action_count": 0,
                        "shell_expansions": [],
                        "pending_actions": [],
                    },
                    "parameter_schema": {"type": "object"},
                    "compatibility": ["claude"],
                    "active_due_to_touched_paths": False,
                }
            ]

        def build_skill_context_payload(self, **_: object) -> dict[str, object]:
            return {"skills": self.build_active_skill_snapshot()}

    class _MCPServiceStub:
        def list_servers(self) -> list[object]:
            return []

    facade = CapabilityFacade(
        skill_service=cast(SkillService, _SkillServiceStub()),
        mcp_service=cast(MCPService, _MCPServiceStub()),
    )

    snapshot = facade.build_skill_snapshot(session_id="session-1")
    first_snapshot = snapshot[0]
    prepared_invocation = cast(dict[str, object], first_snapshot["prepared_invocation"])
    resolved_identity = cast(dict[str, object], first_snapshot["resolved_identity"])

    assert first_snapshot["directory_name"] == "agent-browser"
    assert first_snapshot["invocable"] is True
    assert first_snapshot["allowed_tools"] == ["execute_skill"]
    assert cast(dict[str, object], prepared_invocation["request"])["session_id"] == "session-1"
    assert resolved_identity["relative_path"] == "agent-browser/SKILL.md"


def test_capability_facade_build_skill_context_preserves_selected_skill_payload() -> None:
    class _SkillServiceStub:
        def build_skill_context_payload(self, **_: object) -> dict[str, object]:
            return {
                "skills": [
                    {
                        "id": "skill-1",
                        "directory_name": "agent-browser",
                        "name": "agent-browser",
                        "selected": True,
                        "rank": 1,
                    }
                ],
                "selected_skill": {
                    "id": "skill-1",
                    "directory_name": "agent-browser",
                    "selected": True,
                    "rank": 1,
                },
                "selected_skill_id": "skill-1",
                "selected_skill_rank": 1,
            }

    class _MCPServiceStub:
        def list_servers(self) -> list[object]:
            return []

    facade = CapabilityFacade(
        skill_service=cast(SkillService, _SkillServiceStub()),
        mcp_service=cast(MCPService, _MCPServiceStub()),
    )

    payload = facade.build_skill_context(session_id="session-1")

    assert cast(dict[str, object], payload["selected_skill"])["id"] == "skill-1"
    assert payload["selected_skill_id"] == "skill-1"
    assert payload["selected_skill_rank"] == 1


def test_capability_snapshot_includes_selected_skill_without_shortlist_guessing() -> None:
    class _SkillServiceStub:
        def build_skill_context_payload(self, **_: object) -> dict[str, object]:
            return {
                "skills": [
                    {
                        "id": "skill-1",
                        "directory_name": "agent-browser",
                        "name": "agent-browser",
                        "selected": True,
                        "rank": 1,
                    }
                ],
                "selected_skill": {
                    "id": "skill-1",
                    "directory_name": "agent-browser",
                    "selected": True,
                    "rank": 1,
                },
                "selected_skill_id": "skill-1",
                "selected_skill_rank": 1,
            }

    class _MCPServiceStub:
        def list_servers(self) -> list[object]:
            return []

    facade = CapabilityFacade(
        skill_service=cast(SkillService, _SkillServiceStub()),
        mcp_service=cast(MCPService, _MCPServiceStub()),
    )

    snapshot = facade.build_snapshot(session_id="session-1")

    assert cast(dict[str, object], snapshot["selected_skill"])["id"] == "skill-1"
    assert snapshot["selected_skill_id"] == "skill-1"


def test_capability_facade_builds_mcp_tool_inventory_and_safe_prompt_text() -> None:
    class _SkillServiceStub:
        def build_active_skill_snapshot(self, **_: object) -> list[dict[str, object]]:
            return []

        def build_skill_context_payload(self, **_: object) -> dict[str, object]:
            return {"skills": []}

    class _MCPServiceStub:
        def list_servers(self) -> list[MCPServerRead]:
            return [
                MCPServerRead(
                    id="server-1",
                    name="Burp Suite",
                    source=CompatibilitySource.LOCAL,
                    scope=CompatibilityScope.PROJECT,
                    transport=MCPTransport.STDIO,
                    enabled=True,
                    timeout_ms=30_000,
                    status=MCPServerStatus.CONNECTED,
                    config_path="mcp.json",
                    imported_at=datetime.fromisoformat("2026-01-01T00:00:00+00:00"),
                    capabilities=[
                        MCPCapabilityRead(
                            kind=MCPCapabilityKind.TOOL,
                            name="scan target",
                            title="Scan target",
                            description="Run a focused scan.",
                            input_schema={"type": "string"},
                        ),
                        MCPCapabilityRead(
                            kind=MCPCapabilityKind.PROMPT,
                            name="triage-playbook",
                            title="Triage Playbook",
                        ),
                    ],
                )
            ]

    facade = CapabilityFacade(
        skill_service=cast(SkillService, _SkillServiceStub()),
        mcp_service=cast(MCPService, _MCPServiceStub()),
    )

    inventory = facade.build_mcp_tool_inventory()
    fragment = facade.build_skill_prompt_fragment(session_id="session-1")

    assert inventory == [
        {
            "tool_alias": "mcp__burp_suite__scan_target",
            "server_id": "server-1",
            "server_name": "Burp Suite",
            "source": "local",
            "scope": "project",
            "transport": "stdio",
            "tool_name": "scan target",
            "tool_title": "Scan target",
            "tool_description": "Run a focused scan.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": True,
            },
        }
    ]
    assert "Callable MCP tools:" in fragment
    assert "mcp__burp_suite__scan_target: Burp Suite / scan target" in fragment
    assert "Non-callable MCP resources/prompts/templates" in fragment
    assert "The only callable tool names are" not in fragment
