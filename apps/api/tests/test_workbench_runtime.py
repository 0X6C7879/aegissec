from __future__ import annotations

from typing import Any, cast

from app.agent.prompting import build_workflow_prompting_state
from app.agent.retrieval import RetrievalPipeline
from app.agent.workbench_runtime import (
    WorkbenchRuntimeBuildResult,
    WorkbenchRuntimeProvenance,
    WorkspaceRuntimeState,
    project_workbench_runtime,
)
from app.db.models import (
    Session,
    TaskNode,
    TaskNodeStatus,
    TaskNodeType,
    WorkflowRun,
    WorkflowRunStatus,
)


class _StubRunLogRepository:
    def list_logs(self, **_: object) -> list[dict[str, object]]:
        return []


def test_prompting_continuity_prefers_workbench_runtime_and_keeps_compatibility_views() -> None:
    prompting = build_workflow_prompting_state(
        goal="Authorized assessment",
        template_name="authorized-assessment",
        current_stage="context_collect",
        task_name="context_collect.attack_surface",
        role_prompt="Collect evidence.",
        sub_agent_role_prompt="Stay low-risk.",
        task_description="Map exposed services.",
        retrieval_summary="Session retrieval summary.",
        history_summary="Recent execution summary.",
        memory_summary="Working memory summary.",
        projection_summary="Projection summary.",
        capability_inventory_summary="Inventory summary.",
        capability_schema_summary="Schema summary.",
        capability_prompt_fragment="Prompt fragment.",
        continuity_metadata={
            "source": "runtime_transcript",
            "workbench_runtime": {
                "state": {
                    "active_stage": "wb-stage",
                    "active_tasks": ["wb-task"],
                    "latest_directive": "await_user_input",
                    "active_recall_focus": {"scope": "project", "focus": "wb-focus"},
                    "active_memory_selection": ["memory-wb"],
                    "recent_transcript_highlights": ["wb-highlight"],
                    "active_capability_summary": "wb-capability",
                    "pending_protocol_summary": {"kind": "interaction"},
                    "open_questions": ["wb-question"],
                    "carry_forward_context": "wb-carry",
                }
            },
            "workspace_state": {
                "active_stage": "legacy-stage",
                "active_tasks": ["legacy-task"],
            },
            "workspace_rehydrate": {
                "state": {
                    "active_stage": "legacy-rehydrate-stage",
                    "active_tasks": ["legacy-rehydrate-task"],
                },
                "provenance": {"used_sources": ["legacy"]},
            },
        },
    )

    continuity = cast(dict[str, object], prompting["continuity"])
    assert continuity["source"] == "runtime_transcript"
    assert continuity["workbench_runtime"]
    workspace_state = cast(dict[str, object], continuity["workspace_state"])
    assert workspace_state["active_stage"] == "wb-stage"
    assert workspace_state["active_tasks"] == ["wb-task"]
    assert workspace_state["selected_project_memory_entries"] == ["memory-wb"]

    workspace_rehydrate = cast(dict[str, object], continuity["workspace_rehydrate"])
    workspace_rehydrate_state = cast(dict[str, object], workspace_rehydrate["state"])
    workspace_rehydrate_provenance = cast(dict[str, object], workspace_rehydrate["provenance"])
    assert workspace_rehydrate_state["active_stage"] == "wb-stage"
    assert workspace_rehydrate_state["active_tasks"] == ["wb-task"]
    assert "workbench_runtime" in cast(list[str], workspace_rehydrate_provenance["used_sources"])


def test_retrieval_prefers_workbench_runtime_active_tasks_and_focus(monkeypatch: Any) -> None:
    captured: dict[str, object] = {}

    def _fake_load_memory_manifest(project_id: str) -> list[dict[str, object]]:
        captured["project_id"] = project_id
        return []

    def _fake_rank_memory_manifest_sources(
        manifest: list[dict[str, object]],
        *,
        current_task: str,
        recent_tools: list[str],
        already_surfaced: set[str],
        recall_policy: object,
    ) -> list[dict[str, object]]:
        del manifest, already_surfaced, recall_policy
        captured["manifest_current_task"] = current_task
        captured["manifest_recent_tools"] = list(recent_tools)
        return []

    def _fake_select_relevant_memory_entries(
        project_id: str,
        *,
        current_task: str,
        recent_tools: list[str],
        already_surfaced: set[str],
        recall_policy: object,
    ) -> list[object]:
        del project_id, already_surfaced, recall_policy
        captured["pack_current_task"] = current_task
        captured["pack_recent_tools"] = list(recent_tools)
        return []

    monkeypatch.setattr("app.agent.retrieval.load_memory_manifest", _fake_load_memory_manifest)
    monkeypatch.setattr(
        "app.agent.retrieval.rank_memory_manifest_sources",
        _fake_rank_memory_manifest_sources,
    )
    monkeypatch.setattr(
        "app.agent.retrieval.select_relevant_memory_entries",
        _fake_select_relevant_memory_entries,
    )

    pipeline = RetrievalPipeline(run_log_repository=cast(Any, _StubRunLogRepository()))
    run = WorkflowRun(
        session_id="session-1",
        template_name="authorized-assessment",
        status=WorkflowRunStatus.RUNNING,
        current_stage="context_collect",
        state_json={},
    )
    session = Session(project_id="project-1")
    task = TaskNode(
        workflow_run_id=run.id,
        name="legacy-task",
        node_type=TaskNodeType.TASK,
        status=TaskNodeStatus.READY,
        sequence=1,
        metadata_json={"stage_key": "context_collect"},
    )
    state: dict[str, object] = {
        "workbench_runtime": {
            "state": {
                "active_stage": "wb-stage",
                "active_tasks": ["wb-task"],
                "latest_directive": "continue",
                "active_recall_focus": {"scope": "project", "focus": "wb-focus"},
            }
        },
        "execution_records": [{"command_or_action": "legacy-tool"}],
        "retrieval_manifest": {"project": {"sources": []}},
    }

    pipeline.build(run=run, session=session, state=state, tasks=[task])

    assert captured["manifest_current_task"] == "wb-task"
    assert captured["pack_current_task"] == "wb-task"
    assert captured["manifest_recent_tools"] == ["wb-focus", "legacy-tool"]
    assert captured["pack_recent_tools"] == ["wb-focus", "legacy-tool"]


def test_project_workbench_runtime_projects_legacy_views_from_workbench_state() -> None:
    build = WorkbenchRuntimeBuildResult(
        state=WorkspaceRuntimeState(
            active_stage="wb-stage",
            active_tasks=["wb-task"],
            current_turn_id="turn-1",
            latest_directive="continue",
            active_continuations=[],
            active_recall_focus={"scope": "project", "focus": "wb-focus"},
            active_memory_selection=["memory-wb"],
            recent_transcript_highlights=["wb-highlight"],
            active_capability_summary="wb-capability",
            open_questions=["wb-question"],
            carry_forward_context="wb-carry",
            pending_protocol_summary={"kind": "interaction"},
            latest_assimilation_summary={},
        ),
        provenance=WorkbenchRuntimeProvenance(source="workflow.loop_engine"),
        summary="wb-summary",
    )

    projection = project_workbench_runtime(
        build,
        workspace_rehydrate={
            "state": {
                "active_stage": "legacy-stage",
                "active_tasks": ["legacy-task"],
            },
            "provenance": {
                "used_sources": ["boundary"],
                "boundary_marker": "compact-boundary:1",
            },
            "summary": "legacy-summary",
        },
    )

    workspace_state = cast(dict[str, object], projection["workspace_state"])
    assert workspace_state["active_stage"] == "wb-stage"
    assert workspace_state["active_tasks"] == ["wb-task"]

    workspace_rehydrate = cast(dict[str, object], projection["workspace_rehydrate"])
    workspace_rehydrate_state = cast(dict[str, object], workspace_rehydrate["state"])
    workspace_rehydrate_provenance = cast(dict[str, object], workspace_rehydrate["provenance"])
    assert workspace_rehydrate_state["active_stage"] == "wb-stage"
    assert workspace_rehydrate_provenance["boundary_marker"] == "compact-boundary:1"
    assert cast(list[str], workspace_rehydrate_provenance["used_sources"])[0] == "workbench_runtime"
