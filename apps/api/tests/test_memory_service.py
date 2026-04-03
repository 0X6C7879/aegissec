from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from pytest import MonkeyPatch

from app.agent.context_models import CitationPointer, ContextRecord, RetrievalPack, RetrievalState
from app.agent.memory import MemoryManager
from app.agent.memory_files import ensure_memory_dir, manifest_path
from app.agent.memory_recall import select_relevant_memory_entries
from app.agent.memory_store import load_memory_manifest, read_memory_entry, write_memory_entry
from app.agent.session_memory import SessionMemoryService
from app.db.models import Session


def test_durable_memory_files_create_entry_and_update_manifest(tmp_path: Path) -> None:
    project_id = "project-123"
    ensure_memory_dir(project_id, base_dir=tmp_path)

    entry = write_memory_entry(
        project_id,
        title="Attack Surface Summary",
        summary="Captured externally reachable services.",
        body="Detailed notes about ingress points.",
        tags=["recon", "services"],
        citations=[
            CitationPointer(
                source_kind="execution_record",
                source_id="trace-1",
                label="execute:context_collect.attack_surface",
                trace_id="trace-1",
            )
        ],
        base_dir=tmp_path,
    )

    manifest = load_memory_manifest(project_id, base_dir=tmp_path)
    stored = read_memory_entry(project_id, entry_id=entry.entry_id, base_dir=tmp_path)

    assert manifest_path(project_id, base_dir=tmp_path).exists()
    assert len(manifest) == 1
    assert manifest[0].entry_id == entry.entry_id
    assert manifest[0].summary == "Captured externally reachable services."
    assert stored.title == "Attack Surface Summary"
    assert stored.body == "Detailed notes about ingress points."


def test_session_memory_trigger_thresholds() -> None:
    service = SessionMemoryService()
    state = cast(
        dict[str, object],
        {
            "goal": "Assess ingress and privilege boundaries",
            "current_stage": "context_collect",
            "batch": {"cycle": 2, "status": "completed"},
            "approval": {"required": False},
            "execution_records": [
                {
                    "command_or_action": "execute:context_collect.attack_surface",
                    "summary": "Collected service banners and web roots for all exposed hosts. "
                    * 8,
                    "status": "completed",
                },
                {
                    "command_or_action": "execute:context_collect.existing_evidence",
                    "summary": (
                        "Reviewed previous evidence and correlated it with current host scope. "
                    )
                    * 8,
                    "status": "completed",
                },
            ],
            "findings": [{"title": "Public admin portal", "summary": "Portal still exposed."}],
            "memory_service": {
                "session_summary": {
                    "summary": "Older summary.",
                    "tool_call_count": 0,
                    "source_token_count": 0,
                    "batch_cycle": 0,
                    "current_stage": "scope_guard",
                }
            },
        },
    )

    snapshot = service.update_session_summary(state=state, retrieval=RetrievalState.empty())

    assert snapshot.should_persist is True
    assert snapshot.tool_calls_since_update >= 2
    assert snapshot.tokens_since_update >= service.MIN_TOKENS_BETWEEN_UPDATES
    assert snapshot.summary.startswith("Goal: Assess ingress and privilege boundaries.")


def test_relevant_recall_top_k_and_already_surfaced_dedup(tmp_path: Path) -> None:
    project_id = "project-456"
    now = datetime.now(UTC)
    first = write_memory_entry(
        project_id,
        entry_id="memory-alpha",
        title="Attack Surface Host Map",
        summary="Host map for attack surface and externally exposed services.",
        body="alpha",
        tags=["recon", "attack_surface"],
        citations=[],
        updated_at=now.isoformat(),
        base_dir=tmp_path,
    )
    second = write_memory_entry(
        project_id,
        entry_id="memory-beta",
        title="Privilege Escalation Notes",
        summary="Privilege escalation opportunities on Linux bastion.",
        body="beta",
        tags=["privesc"],
        citations=[],
        updated_at=(now - timedelta(days=2)).isoformat(),
        base_dir=tmp_path,
    )
    third = write_memory_entry(
        project_id,
        entry_id="memory-gamma",
        title="Credential Reuse Findings",
        summary="Credential reuse across web and SSH entry points.",
        body="gamma",
        tags=["credentials", "ssh"],
        citations=[],
        updated_at=(now - timedelta(days=10)).isoformat(),
        base_dir=tmp_path,
    )

    selected = select_relevant_memory_entries(
        project_id,
        current_task="context_collect attack_surface external services",
        recent_tools=["execute:context_collect.attack_surface"],
        already_surfaced={first.entry_id},
        top_k=2,
        base_dir=tmp_path,
    )

    selected_ids = [entry.entry_id for entry in selected]
    assert len(selected) == 2
    assert first.entry_id not in selected_ids
    assert second.entry_id in selected_ids or third.entry_id in selected_ids


def test_memory_manager_does_not_persist_synthetic_retrieval_bridge(
    monkeypatch: MonkeyPatch,
) -> None:
    persisted_entry_ids: list[str] = []

    def fake_write_memory_entry(project_id: str, **kwargs: object) -> object:
        assert project_id == "project-789"
        entry_id = cast(str, kwargs["entry_id"])
        persisted_entry_ids.append(entry_id)
        return SimpleNamespace(entry_id=entry_id)

    monkeypatch.setattr("app.agent.memory.write_memory_entry", fake_write_memory_entry)

    manager = MemoryManager()
    state = cast(
        dict[str, object],
        {
            "batch": {"cycle": 1, "status": "completed"},
            "execution_records": [
                {
                    "id": "trace-raw-1",
                    "batch_cycle": 1,
                    "command_or_action": "execute:context_collect.attack_surface",
                    "summary": "Collected externally reachable services.",
                    "status": "completed",
                    "task_node_id": "task-1",
                }
            ],
            "memory_service": {},
        },
    )
    retrieval = RetrievalState(
        session_local=RetrievalPack(
            pack_id="retrieval:session_local",
            scope="session_local",
            status="ready",
            summary="Session-local retrieval ready.",
            items=[
                ContextRecord(
                    record_id="retrieval:session:1",
                    title="Recent evidence",
                    summary="Most relevant recent retrieval result.",
                    kind="session_local",
                    citations=[],
                    metadata={},
                )
            ],
        ),
        project=RetrievalPack.empty(
            scope="project",
            status="ready",
            summary="Project retrieval ready.",
        ),
        capability=RetrievalPack.empty(
            scope="capability",
            status="ready",
            summary="Capability retrieval ready.",
        ),
    )

    memory_state = manager.build(
        session=cast(Session, SimpleNamespace(project_id="project-789")),
        state=state,
        retrieval=retrieval,
    )

    assert "memory:working:distilled:retrieval-bridge" not in persisted_entry_ids
    assert "memory:working:distilled:trace-raw-1" in persisted_entry_ids
    assert any(entry.kind == "retrieval_bridge" for entry in memory_state.working.distilled_entries)
    assert all(
        promotion.entry_id != "memory:working:distilled:retrieval-bridge"
        for promotion in memory_state.promotions
        if promotion.to_layer == "project"
    )
