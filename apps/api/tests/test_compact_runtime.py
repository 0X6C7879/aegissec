from typing import Any, cast

from app.agent.compact_runtime import CompactRuntimeService
from app.agent.context_models import ContextProjection
from app.agent.post_compact_reinjection import PostCompactReinjectionService


def test_compact_runtime_triggers_and_creates_boundary() -> None:
    service = CompactRuntimeService()
    mutable_state: dict[str, object] = {
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
    }

    runtime_state = service.build_runtime_state(
        mutable_state=mutable_state,
        retrieval_summary="Session retrieval summary.",
        memory_summary="Session memory summary.",
        history_summary="Recent execution summary.",
        projection=ContextProjection.empty(),
        active_task_name="context_collect.attack_surface",
        current_stage="context_collect",
    )

    assert runtime_state["triggered"] is True
    assert runtime_state["compacted"] is True
    assert str(runtime_state["boundary_marker"]).startswith("compact-boundary:")
    assert "Boundary marker:" in str(runtime_state["compact_summary"])
    retained_live_state = cast(dict[str, Any], runtime_state["retained_live_state"])
    assert retained_live_state["current_stage"] == "context_collect"
    assert retained_live_state["current_task"] == "context_collect.attack_surface"
    compact_metadata = cast(dict[str, Any], runtime_state["compact_metadata"])
    metrics = cast(dict[str, Any], compact_metadata["metrics"])
    assert metrics["message_count"] >= 1
    assert metrics["execution_record_count"] >= 1


def test_post_compact_reinjection_restores_required_fragments() -> None:
    reinjection = PostCompactReinjectionService().build_reinjection(
        compact_runtime={
            "compacted": True,
            "boundary_marker": "compact-boundary:1",
            "compact_summary": "Compact summary text.",
        },
        retrieval_summary="Current retrieval summary.",
        session_memory_summary="Latest session memory summary.",
        current_stage="analysis",
        task_name="analyze.evidence",
        capability_inventory_summary="Loaded skills inventory:\n- agent-browser",
        capability_schema_summary="Capability schema summary.",
        capability_prompt_fragment="Capability prompt fragment.",
    )

    assert reinjection["compact_applied"] is True
    assert reinjection["boundary_marker"] == "compact-boundary:1"
    assert "Current stage: analysis | Current task: analyze.evidence" in str(reinjection["summary"])
    assert "Current retrieval summary." in str(reinjection["summary"])
    assert "Latest session memory summary." in str(reinjection["summary"])
    assert "Loaded skills inventory:" in str(reinjection["summary"])
    fragments = cast(dict[str, Any], reinjection["fragments"])
    assert fragments["capability_inventory_summary"]
    assert fragments["capability_schema_summary"]
    assert fragments["capability_prompt_fragment"]
    assert fragments["active_tool_summary"]
