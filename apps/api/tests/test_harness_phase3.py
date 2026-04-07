from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

from app.db.models import MessageKind, MessageRole

HarnessCompactService = importlib.import_module("app.harness.compact").HarnessCompactService
HarnessMemoryService = importlib.import_module("app.harness.memory").HarnessMemoryService
HarnessPromptAssembler = importlib.import_module("app.harness.prompts").HarnessPromptAssembler
HarnessSessionState = importlib.import_module("app.harness.state").HarnessSessionState


class _FakeSkillService:
    def list_loaded_skills_for_agent(self, **_: object) -> list[object]:
        return []


class _FakeCapabilityFacade:
    def build_prompt_fragments(self, **_: object) -> dict[str, str]:
        return {
            "inventory_summary": "## Capabilities\n- execute_kali_command",
            "schema_summary": "tool schema summary",
            "prompt_fragment": "tool prompt fragment",
        }

    def build_mcp_tool_inventory(self) -> list[dict[str, object]]:
        return []


class _FakeRepository:
    def __init__(self, messages: list[SimpleNamespace]) -> None:
        self._messages = messages

    def build_conversation_context(self, **_: object) -> list[SimpleNamespace]:
        return list(self._messages)


def _build_message(
    role: MessageRole,
    content: str,
    *,
    metadata_json: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        role=role,
        content=content,
        message_kind=MessageKind.MESSAGE,
        attachments_json=[],
        metadata_json=metadata_json or {},
    )


def test_harness_prompt_assembler_includes_memory_fragments(tmp_path: Path) -> None:
    memory_service = HarnessMemoryService(base_dir=tmp_path)
    seeded_state = HarnessSessionState(session_id="sess-1", memory_key="sess-1")
    memory_service.write_compact_boundary(
        session_state=seeded_state,
        title="Known finding",
        summary="Remember previous reconnaissance details.",
        body="Target host example.internal exposed a useful login panel.",
        tags=["finding", "compact"],
    )
    assembler = HarnessPromptAssembler(
        capability_facade=_FakeCapabilityFacade(),
        skill_service=_FakeSkillService(),
        memory_service=memory_service,
    )
    session = SimpleNamespace(
        id="sess-1",
        project_id=None,
        current_phase="recon",
        goal="Enumerate the target safely",
        scenario_type="chat",
    )
    prior_assistant = _build_message(
        MessageRole.ASSISTANT,
        "Previous observation",
        metadata_json={
            "semantic_state": {
                "active_hypotheses": ["hypothesis:login-panel"],
                "evidence_ids": ["runtime:123"],
                "graph_hints": [{"graph_type": "attack", "stable_key": "runtime:123"}],
                "artifacts": ["reports/scan.txt"],
                "recent_entities": ["example.internal"],
                "recent_tools": ["execute_kali_command"],
                "reason": "Prior runtime evidence.",
            }
        },
    )
    user_message = _build_message(MessageRole.USER, "Inspect the exposed login panel")
    assistant_message = _build_message(MessageRole.ASSISTANT, "")
    repository = _FakeRepository([prior_assistant, user_message])

    assembly = assembler.build(
        session=session,
        repository=repository,
        user_message=user_message,
        assistant_message=assistant_message,
        branch_id=None,
        total_token_budget=12000,
    )

    assert assembly.session_state.memory_key == "sess-1"
    assert assembly.session_state.retrieval_manifest.recalled_entry_ids
    assert "## Relevant Memory" in assembly.skill_context_prompt
    assert "Known finding" in assembly.skill_context_prompt
    assert (tmp_path / "sess-1" / "targets").exists()
    assert assembly.conversation_messages[-1].content == "Inspect the exposed login panel"
    assert assembly.session_state.semantic.active_hypotheses == ["hypothesis:login-panel"]
    assert assembly.session_state.semantic.evidence_ids == ["runtime:123"]
    assert assembly.session_state.semantic.recent_entities == ["example.internal"]
    assert assembly.session_state.semantic.recent_tools == ["execute_kali_command"]


def test_harness_compact_service_creates_compact_boundary(tmp_path: Path) -> None:
    memory_service = HarnessMemoryService(base_dir=tmp_path)
    session_state = HarnessSessionState(
        session_id="sess-2",
        memory_key=memory_service.memory_key_for_session("sess-2", None),
        current_phase="exploit",
        goal="Continue the task",
    )
    memory_service.ensure_layout(session_state.memory_key)
    compact_service = HarnessCompactService(memory_service=memory_service, retained_tail=4)
    messages = [
        {"role": "system", "content": "system instructions"},
        *[
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"message-{index} " + ("x" * 3000),
            }
            for index in range(16)
        ],
    ]

    compacted = compact_service.maybe_compact(
        messages=messages,
        session_state=session_state,
        render_compact_message=lambda fragment: {"role": "user", "content": fragment},
        turn_count=7,
    )

    assert session_state.compaction.mode == "full"
    assert session_state.compaction.durable_artifact_ref is not None
    assert session_state.compaction.archived_message_count > 0
    assert len(compacted) < len(messages)
    session_state.semantic.active_hypotheses = ["hypothesis:service-account"]
    session_state.semantic.evidence_ids = ["runtime:run-1"]
    session_state.semantic.artifacts = ["reports/proof.txt"]
    session_state.semantic.recent_tools = ["execute_kali_command"]
    compacted = compact_service.maybe_compact(
        messages=messages,
        session_state=session_state,
        render_compact_message=lambda fragment: {"role": "user", "content": fragment},
        turn_count=8,
    )
    assert any(
        isinstance(message.get("content"), str) and "## Compacted History" in message["content"]
        for message in compacted
    )
    assert any(
        isinstance(message.get("content"), str)
        and "active_hypotheses: hypothesis:service-account" in message["content"]
        for message in compacted
    )
