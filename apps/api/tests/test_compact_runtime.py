from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from app.agent.compact_runtime import CompactRuntimeService
from app.agent.context_models import ContextProjection
from app.agent.memory_recall import select_relevant_memory_entries
from app.agent.memory_store import record_memory_entry_surfaced, write_memory_entry
from app.agent.post_compact_reinjection import PostCompactReinjectionService
from app.agent.recall_policy import RecallPolicy


def test_compact_runtime_triggers_and_creates_boundary() -> None:
    service = CompactRuntimeService()
    mutable_state: dict[str, object] = {
        "runtime_transcript": {
            "turns": [],
            "deltas": [],
            "tool_use_records": [],
            "tool_result_records": [],
            "compact_events": [],
            "reinjection_events": [],
            "last_directive": "continue",
        },
        "messages": [
            {"role": "user", "content": "Need continuity after compact."},
            {"role": "assistant", "content": "Working through the evidence trail."},
        ],
        "execution_records": [
            {
                "task_name": "context_collect.attack_surface",
                "status": "completed",
                "summary": "Mapped exposed services.",
            }
        ],
        "compaction": {
            "runtime": {
                "config": {
                    "rough_token_threshold": 1,
                    "message_count_threshold": 1,
                    "execution_record_threshold": 1,
                }
            }
        },
        "pause": {
            "active": {
                "kind": "interaction",
                "pause_reason": "awaiting user input",
                "resume_condition": "resume with user input",
                "task_id": "task-1",
                "task_name": "context_collect.attack_surface",
            }
        },
        "retrieval_manifest": {
            "project": {
                "sources": [
                    {"source_id": "memory-a"},
                    {"source_id": "memory-b"},
                ]
            }
        },
        "assistant_turn": {
            "outcome": {
                "resulting_directive": "await_user_input",
                "unresolved_questions": ["Need operator answer"],
                "carry_forward_context": "Awaiting scoped host answer.",
            }
        },
    }

    runtime_state = service.build_runtime_state(
        mutable_state=mutable_state,
        retrieval_summary="Session retrieval summary.",
        memory_summary="Session memory summary.",
        history_summary="Recent execution summary.",
        projection=ContextProjection.empty(),
        active_task_name="context_collect.attack_surface",
        active_tasks=["context_collect.attack_surface"],
        current_stage="context_collect",
        latest_turn_directive="await_user_input",
        pending_protocol={
            "kind": "interaction",
            "pause_reason": "awaiting user input",
            "resume_condition": "resume with user input",
            "task_id": "task-1",
            "task_name": "context_collect.attack_surface",
        },
        active_capability_inventory_summary="Loaded skills inventory:\n- agent-browser",
        selected_project_memory_entries=["memory-a", "memory-b"],
        current_retrieval_focus={"scope": "project", "focus": "memory-a"},
        cycle_id="cycle-1",
    )

    assert runtime_state["triggered"] is True
    assert runtime_state["compacted"] is True
    assert str(runtime_state["boundary_marker"]).startswith("compact-boundary:")
    assert "Boundary marker:" in str(runtime_state["compact_summary"])
    retained_live_state = cast(dict[str, Any], runtime_state["retained_live_state"])
    assert retained_live_state["current_stage"] == "context_collect"
    assert retained_live_state["current_task"] == "context_collect.attack_surface"
    workspace_state = cast(dict[str, Any], retained_live_state["workspace_state"])
    assert workspace_state["active_stage"] == "context_collect"
    assert workspace_state["active_tasks"] == ["context_collect.attack_surface"]
    assert workspace_state["latest_turn_directive"] == "await_user_input"
    assert cast(dict[str, Any], workspace_state["pending_protocol"])["kind"] == "interaction"
    assert cast(list[str], workspace_state["selected_project_memory_entries"]) == [
        "memory-a",
        "memory-b",
    ]
    assert isinstance(workspace_state["recent_transcript_highlights"], list)
    assert isinstance(workspace_state["active_capability_inventory_summary"], str)
    assert isinstance(workspace_state["current_retrieval_focus"], dict)
    assert isinstance(workspace_state["open_questions"], list)
    assert isinstance(workspace_state["carry_forward_context"], str)
    compact_metadata = cast(dict[str, Any], runtime_state["compact_metadata"])
    metrics = cast(dict[str, Any], compact_metadata["metrics"])
    assert metrics["message_count"] >= 1
    assert metrics["execution_record_count"] >= 1
    runtime_transcript = cast(dict[str, Any], mutable_state["runtime_transcript"])
    compact_events = cast(list[dict[str, Any]], runtime_transcript["compact_events"])
    assert compact_events
    assert compact_events[-1]["boundary_marker"] == runtime_state["boundary_marker"]


def test_post_compact_reinjection_restores_required_fragments() -> None:
    mutable_state: dict[str, object] = {
        "runtime_transcript": {
            "turns": [],
            "deltas": [],
            "tool_use_records": [],
            "tool_result_records": [],
            "compact_events": [],
            "reinjection_events": [],
            "last_directive": "continue",
        }
    }
    reinjection = PostCompactReinjectionService().build_reinjection(
        compact_runtime={
            "compacted": True,
            "boundary_marker": "compact-boundary:1",
            "compact_summary": "Compact summary text.",
            "retained_live_state": {
                "current_stage": "restored-analysis",
                "current_task": "restore.evidence",
                "workspace_state": {
                    "active_stage": "restored-analysis",
                    "active_tasks": ["restore.evidence"],
                    "latest_turn_directive": "await_user_input",
                    "pending_protocol": {
                        "kind": "interaction",
                        "pause_reason": "awaiting user input",
                        "resume_condition": "provide answer",
                        "task_id": "task-restore",
                        "task_name": "restore.evidence",
                    },
                    "active_capability_inventory_summary": (
                        "Loaded skills inventory:\n- agent-browser"
                    ),
                    "recent_transcript_highlights": ["Need continuity after compact."],
                    "selected_project_memory_entries": ["memory-restored"],
                    "current_retrieval_focus": {"scope": "project", "focus": "memory-restored"},
                },
            },
        },
        retrieval_summary="Current retrieval summary.",
        session_memory_summary="Latest session memory summary.",
        current_stage="analysis",
        task_name="analyze.evidence",
        capability_inventory_summary="Loaded skills inventory:\n- agent-browser",
        capability_schema_summary="Capability schema summary.",
        capability_prompt_fragment="Capability prompt fragment.",
        mutable_state=mutable_state,
        cycle_id="cycle-1",
    )

    assert reinjection["compact_applied"] is True
    assert reinjection["boundary_marker"] == "compact-boundary:1"
    assert "Current stage: restored-analysis | Current task: restore.evidence" in str(
        reinjection["summary"]
    )
    assert "Current retrieval summary." in str(reinjection["summary"])
    assert "Latest session memory summary." in str(reinjection["summary"])
    assert "Loaded skills inventory:" in str(reinjection["summary"])
    fragments = cast(dict[str, Any], reinjection["fragments"])
    assert fragments["capability_inventory_summary"]
    assert fragments["capability_schema_summary"]
    assert fragments["capability_prompt_fragment"]
    assert fragments["active_tool_summary"]
    assert fragments["task_stage_marker"] == (
        "Current stage: restored-analysis | Current task: restore.evidence"
    )
    provenance = cast(dict[str, Any], reinjection["provenance"])
    assert provenance["restored_from_boundary"] is True
    assert cast(dict[str, Any], fragments["workspace_state"])["active_stage"] == "restored-analysis"
    assert cast(dict[str, Any], provenance["workspace_state"])["latest_turn_directive"] == (
        "await_user_input"
    )
    workspace_rehydrate = cast(dict[str, Any], provenance["workspace_rehydrate"])
    assert cast(dict[str, Any], workspace_rehydrate["state"])["active_stage"] == "restored-analysis"
    assert cast(dict[str, Any], workspace_rehydrate["provenance"])["used_sources"] == ["boundary"]
    assert "Workspace continuity:" in str(reinjection["summary"])
    runtime_transcript = cast(dict[str, Any], mutable_state["runtime_transcript"])
    reinjection_events = cast(list[dict[str, Any]], runtime_transcript["reinjection_events"])
    assert len(reinjection_events) == 1
    assert reinjection_events[0]["boundary_marker"] == "compact-boundary:1"


def test_durable_surfaced_history_survives_compact_for_recall_policy(tmp_path: Path) -> None:
    project_id = "project-compact-memory"
    now = datetime.now(UTC)
    first = write_memory_entry(
        project_id,
        entry_id="memory-compact-old",
        title="Compact Boundary Recall",
        summary="Recall item surfaced before compact.",
        body="old",
        tags=["compact"],
        citations=[],
        updated_at=now.isoformat(),
        base_dir=tmp_path,
    )
    second = write_memory_entry(
        project_id,
        entry_id="memory-compact-fresh",
        title="Fresh Compact Recall",
        summary="Fresh recall item after compact.",
        body="fresh",
        tags=["compact"],
        citations=[],
        updated_at=now.isoformat(),
        base_dir=tmp_path,
    )
    record_memory_entry_surfaced(
        project_id,
        entry_id=first.entry_id,
        scope="session_derived",
        source_trace="trace-before-compact",
        source_pack="project",
        base_dir=tmp_path,
    )

    selected = select_relevant_memory_entries(
        project_id,
        current_task="compact recall",
        recent_tools=["compact-boundary"],
        already_surfaced=set(),
        recall_policy=RecallPolicy(top_k=1),
        base_dir=tmp_path,
    )

    assert [entry.entry_id for entry in selected] == [second.entry_id]


def test_compact_runtime_refreshes_boundary_when_workspace_changes_without_metric_change() -> None:
    service = CompactRuntimeService()
    mutable_state: dict[str, object] = {
        "runtime_transcript": {
            "turns": [],
            "deltas": [],
            "tool_use_records": [],
            "tool_result_records": [],
            "compact_events": [],
            "reinjection_events": [],
            "last_directive": "continue",
        },
        "messages": [{"role": "user", "content": "same metrics"}],
        "execution_records": [{"task_name": "task-a", "status": "completed", "summary": "same"}],
        "pause": {"active": None},
        "retrieval_manifest": {"project": {"sources": [{"source_id": "memory-a"}]}},
        "assistant_turn": {"outcome": {"resulting_directive": "continue"}},
        "compaction": {
            "runtime": {
                "config": {
                    "rough_token_threshold": 1,
                    "message_count_threshold": 1,
                    "execution_record_threshold": 1,
                }
            }
        },
    }

    first = service.build_runtime_state(
        mutable_state=mutable_state,
        retrieval_summary="same retrieval",
        memory_summary="same memory",
        history_summary="same history",
        projection=ContextProjection.empty(),
        active_task_name="task-a",
        active_tasks=["task-a"],
        current_stage="stage-a",
        latest_turn_directive="continue",
        pending_protocol={},
        active_capability_inventory_summary="inventory-a",
        selected_project_memory_entries=["memory-a"],
        current_retrieval_focus={"scope": "project", "focus": "memory-a"},
        cycle_id="cycle-1",
    )
    first_marker = str(first["boundary_marker"])

    mutable_state["pause"] = {
        "active": {
            "kind": "approval",
            "pause_reason": "approval changed",
            "resume_condition": "approve",
            "task_id": "task-b",
            "task_name": "task-b",
        }
    }

    second = service.build_runtime_state(
        mutable_state=mutable_state,
        retrieval_summary="same retrieval",
        memory_summary="same memory",
        history_summary="same history",
        projection=ContextProjection.empty(),
        active_task_name="task-a",
        active_tasks=["task-a"],
        current_stage="stage-a",
        latest_turn_directive="continue",
        pending_protocol={
            "kind": "approval",
            "pause_reason": "approval changed",
            "resume_condition": "approve",
            "task_id": "task-b",
            "task_name": "task-b",
        },
        active_capability_inventory_summary="inventory-a",
        selected_project_memory_entries=["memory-a"],
        current_retrieval_focus={"scope": "project", "focus": "memory-a"},
        cycle_id="cycle-2",
    )

    assert str(second["boundary_marker"]) != first_marker
