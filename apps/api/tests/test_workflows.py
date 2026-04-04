from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient

from app.agent.context_models import ContextSnapshot
from app.agent.coordinator import Coordinator
from app.agent.executor import ExecutionResult, Executor
from app.agent.loop_engine import WorkflowLoopEngine
from app.agent.reflector import Reflector
from app.agent.selection import RunnableSelection, SelectedTask, WorkflowRunnableSelector
from app.agent.tool_registry import (
    NoOpToolExecutionHooks,
    ToolAccessMode,
    ToolCapability,
    ToolCategory,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolPolicyDecision,
    ToolRegistry,
    ToolSafetyProfile,
    ToolSpec,
)
from app.agent.tool_scheduler import WorkflowToolScheduler
from app.agent.workflow import WorkflowExecutionContext
from app.db.models import (
    Session,
    TaskNode,
    TaskNodeStatus,
    TaskNodeType,
    WorkflowRun,
    WorkflowRunStatus,
)
from tests.utils import api_data


def _create_session(client: TestClient, *, goal: str | None = None) -> str:
    payload: dict[str, Any] = {}
    if goal is not None:
        payload["goal"] = goal
    response = client.post("/api/sessions", json=payload)
    assert response.status_code == 201
    return cast(str, api_data(response)["id"])


def _start_workflow(
    client: TestClient,
    session_id: str,
    *,
    template_name: str = "authorized-assessment",
) -> dict[str, Any]:
    response = client.post(
        f"/api/workflows/{template_name}/start",
        json={"session_id": session_id},
    )
    assert response.status_code == 201
    return cast(dict[str, Any], api_data(response))


def _workflow_tasks_by_name(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {cast(str, task["name"]): task for task in cast(list[dict[str, Any]], workflow["tasks"])}


class _StubRunLogRepository:
    def list_logs(self, **_: object) -> list[dict[str, Any]]:
        return []


def test_start_workflow_persists_structured_plan_and_dag_task_metadata(client: TestClient) -> None:
    session_goal = "Assess authorized web target for low-risk exploitability paths"
    session_id = _create_session(client, goal=session_goal)

    workflow = _start_workflow(client, session_id)

    assert workflow["session_id"] == session_id
    assert workflow["template_name"] == "authorized-assessment"
    assert workflow["status"] == "running"
    assert workflow["current_stage"] == "scope_guard"

    state = cast(dict[str, Any], workflow["state"])
    assert state["goal"] == session_goal
    assert state["runtime_policy"] == {}
    assert cast(dict[str, Any], state["plan"])["summary"]
    assert len(cast(list[dict[str, Any]], cast(dict[str, Any], state["plan"])["nodes"])) > 9

    tasks = _workflow_tasks_by_name(workflow)
    assert "scope_guard" in tasks
    assert "context_collect.attack_surface" in tasks
    assert "context_collect.existing_evidence" in tasks
    assert "hypothesis_build.hypothesis_draft" in tasks
    assert tasks["scope_guard"]["node_type"] == "stage"
    assert tasks["context_collect.attack_surface"]["node_type"] == "task"

    hypothesis_metadata = cast(
        dict[str, Any], tasks["hypothesis_build.hypothesis_draft"]["metadata"]
    )
    dependency_ids = cast(list[str], hypothesis_metadata["depends_on_task_ids"])
    assert len(dependency_ids) == 3
    assert hypothesis_metadata["approval_required"] is False

    safe_validation_metadata = cast(
        dict[str, Any],
        tasks["safe_validation.validate_primary_hypothesis"]["metadata"],
    )
    assert hypothesis_metadata["execution_state"] == "pending"
    assert safe_validation_metadata["approval_required"] is True
    assert safe_validation_metadata["workflow_phase"] == "validation"
    assert safe_validation_metadata["template_kinds"] == [
        "recon",
        "analysis",
        "validation",
        "reporting",
    ]


def test_workflow_start_reuses_cached_capability_snapshot(client: TestClient) -> None:
    session_id = _create_session(client, goal="Cache capability snapshot")
    _start_workflow(client, session_id)

    second_start_response = client.post(
        "/api/workflows/authorized-assessment/start",
        json={"session_id": session_id},
    )
    assert second_start_response.status_code == 201

    history_response = client.get(
        f"/api/sessions/{session_id}/history",
        params={"source": "capability_facade", "event_type": "capability.snapshot.cache_hit"},
    )
    assert history_response.status_code == 200
    assert history_response.json()["meta"]["pagination"]["total"] >= 1


def test_advance_workflow_blocks_for_approval_and_supports_resume(client: TestClient) -> None:
    session_id = _create_session(client, goal="Run an authorized assessment with approval gates")
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    approval_response = None
    for _ in range(20):
        response = client.post(f"/api/workflows/{run_id}/advance", json={})
        if response.status_code == 409:
            approval_response = response
            break
        assert response.status_code == 200

    assert approval_response is not None
    assert approval_response.json()["detail"] == "Approval required."

    blocked_detail = client.get(f"/api/workflows/{run_id}")
    blocked_payload = cast(dict[str, Any], api_data(blocked_detail))
    assert blocked_payload["status"] == "needs_approval"
    blocked_state = cast(dict[str, Any], blocked_payload["state"])
    blocked_pause = cast(dict[str, Any], blocked_state["pause"])
    pending_approvals = cast(list[dict[str, Any]], blocked_pause["pending_approvals"])
    assert pending_approvals
    active_pause = cast(dict[str, Any], blocked_pause["active"])
    assert active_pause["kind"] == "approval"
    assert active_pause["resume_payload"]["resolution_kind"] == "approval"
    runtime_transcript = cast(dict[str, Any], blocked_state["runtime_transcript"])
    assert set(runtime_transcript.keys()) == {
        "turns",
        "deltas",
        "tool_use_records",
        "tool_result_records",
        "compact_events",
        "reinjection_events",
        "last_directive",
    }
    assert any(
        any(
            block["metadata"].get("event_type") == "approval_pending"
            for block in cast(list[dict[str, Any]], delta.get("assistant_blocks", []))
        )
        for delta in cast(list[dict[str, Any]], runtime_transcript["deltas"])
    )
    blocked_continuity = cast(
        dict[str, Any], cast(dict[str, Any], blocked_state["context"])["prompting"]
    )["continuity"]
    assert blocked_continuity["pending_protocol_kind"] == "approval"
    assert blocked_continuity["pending_protocol_pause_reason"]
    assert blocked_continuity["pending_protocol_resume_condition"]

    tasks = _workflow_tasks_by_name(blocked_payload)
    assert tasks["safe_validation.validate_primary_hypothesis"]["status"] == "blocked"
    assert tasks["safe_validation.validate_primary_hypothesis"]["metadata"]["execution_state"] == (
        "waiting_approval"
    )

    approved = client.post(
        f"/api/workflows/{run_id}/advance",
        json={
            "approve": True,
            "resume_token": active_pause["continuation_token"],
            "resolution_payload": {"operator": "test-client", "decision": "approve"},
        },
    )
    assert approved.status_code == 200
    approved_payload = cast(dict[str, Any], api_data(approved))
    assert approved_payload["status"] == "running"
    approved_state = cast(dict[str, Any], approved_payload["state"])
    approved_pause = cast(dict[str, Any], approved_state["pause"])
    assert not cast(list[dict[str, Any]], approved_pause["pending_approvals"])
    resolved_approvals = cast(list[dict[str, Any]], approved_pause["resolved_approvals"])
    assert resolved_approvals
    assert cast(dict[str, Any], resolved_approvals[-1]["resolution"])["approved"] is True
    approved_runtime = cast(dict[str, Any], approved_state["runtime_transcript"])
    assert any(
        any(
            block["metadata"].get("event_type") == "approval_resolved"
            for block in cast(list[dict[str, Any]], delta.get("assistant_blocks", []))
        )
        for delta in cast(list[dict[str, Any]], approved_runtime["deltas"])
    )
    approved_continuity = cast(
        dict[str, Any], cast(dict[str, Any], approved_state["context"])["prompting"]
    )["continuity"]
    assert approved_continuity["resolved_protocol_kind"] == "approval"
    assert (
        cast(dict[str, Any], approved_continuity["resolved_protocol_payload"])["approved"] is True
    )
    approved_tasks = _workflow_tasks_by_name(approved_payload)
    assert approved_tasks["safe_validation.validate_primary_hypothesis"]["status"] == "completed"
    assert (
        approved_tasks["safe_validation.validate_primary_hypothesis"]["metadata"]["execution_state"]
        == "success"
    )


def test_workflow_ask_user_question_protocol_pause_and_resume_updates_state_and_transcript() -> (
    None
):
    import asyncio

    class StubSessionRepository:
        def get_session(self, session_id: str) -> Session | None:
            del session_id
            return None

    class StubCapabilityFacade:
        def build_prompt_fragments(self, **_: object) -> dict[str, str]:
            return {
                "inventory_summary": "",
                "schema_summary": "",
                "prompt_fragment": "",
            }

    engine = WorkflowLoopEngine(
        executor=Executor(),
        reflector=Reflector(),
        max_active_execution_records=10,
        max_active_messages=10,
        session_repository=cast(Any, StubSessionRepository()),
        run_log_repository=cast(Any, _StubRunLogRepository()),
        graph_repository=cast(Any, object()),
        capability_facade=cast(Any, StubCapabilityFacade()),
    )

    run = WorkflowRun(
        session_id="session-ask-user",
        template_name="authorized-assessment",
        status=WorkflowRunStatus.RUNNING,
        current_stage="context_collect",
        state_json={"goal": "Need scope clarification", "runtime_policy": {}},
    )
    task = TaskNode(
        workflow_run_id=run.id,
        name="scope_clarification",
        node_type=TaskNodeType.TASK,
        status=TaskNodeStatus.READY,
        sequence=1,
        metadata_json={
            "stage_key": "context_collect",
            "workflow_tool": "workflow.ask_user_question",
            "question": "Which host should the agent validate first?",
            "description": "Ask the operator to clarify the first in-scope host.",
        },
    )

    blocked_result = asyncio.run(
        engine.advance(
            run=run,
            tasks=[task],
            approve=False,
            user_input=None,
            resume_token=None,
            resolution_payload=None,
        )
    )

    assert blocked_result.status is WorkflowRunStatus.BLOCKED
    blocked_state = blocked_result.state
    pause_state = cast(dict[str, Any], blocked_state["pause"])
    pending_interactions = cast(list[dict[str, Any]], pause_state["pending_interactions"])
    assert pending_interactions
    active_pause = cast(dict[str, Any], pause_state["active"])
    assert active_pause["kind"] == "interaction"
    assert active_pause["resume_payload"]["resolution_kind"] == "interaction"
    blocked_runtime = cast(dict[str, Any], blocked_state["runtime_transcript"])
    assert set(blocked_runtime.keys()) == {
        "turns",
        "deltas",
        "tool_use_records",
        "tool_result_records",
        "compact_events",
        "reinjection_events",
        "last_directive",
    }
    assert any(
        any(
            block["metadata"].get("event_type") == "interaction_pending"
            for block in cast(list[dict[str, Any]], delta.get("assistant_blocks", []))
        )
        for delta in cast(list[dict[str, Any]], blocked_runtime["deltas"])
    )
    blocked_continuity = cast(
        dict[str, Any], cast(dict[str, Any], blocked_state["context"])["prompting"]
    )["continuity"]
    assert blocked_continuity["pending_protocol_kind"] == "interaction"
    assert blocked_continuity["pending_protocol_pause_reason"]
    assert blocked_continuity["pending_protocol_resume_condition"]

    run.state_json = blocked_state
    resumed_result = asyncio.run(
        engine.advance(
            run=run,
            tasks=[task],
            approve=False,
            user_input="Validate host app.internal.example first.",
            resume_token=active_pause["continuation_token"],
            resolution_payload={"provided_by": "operator"},
        )
    )

    assert resumed_result.status is WorkflowRunStatus.DONE
    resumed_state = resumed_result.state
    resumed_pause = cast(dict[str, Any], resumed_state["pause"])
    assert not cast(list[dict[str, Any]], resumed_pause["pending_interactions"])
    resolved_interactions = cast(list[dict[str, Any]], resumed_pause["resolved_interactions"])
    assert resolved_interactions
    resolution = cast(dict[str, Any], resolved_interactions[-1]["resolution"])
    assert resolution["user_input"] == "Validate host app.internal.example first."
    resumed_runtime = cast(dict[str, Any], resumed_state["runtime_transcript"])
    assert any(
        any(
            block["metadata"].get("event_type") == "interaction_resolved"
            for block in cast(list[dict[str, Any]], delta.get("assistant_blocks", []))
        )
        for delta in cast(list[dict[str, Any]], resumed_runtime["deltas"])
    )
    resumed_continuity = cast(
        dict[str, Any], cast(dict[str, Any], resumed_state["context"])["prompting"]
    )["continuity"]
    assert resumed_continuity["resolved_protocol_kind"] == "interaction"
    assert (
        cast(dict[str, Any], resumed_continuity["resolved_protocol_payload"])["user_input"]
        == "Validate host app.internal.example first."
    )
    execution_records = cast(list[dict[str, Any]], resumed_state["execution_records"])
    assert len(execution_records) == 2
    assert execution_records[0]["status"] == "blocked"
    assert execution_records[1]["status"] == "completed"


def test_workflow_rehydrates_workspace_state_into_prompting_and_assistant_turn_after_compact() -> (
    None
):
    import asyncio

    class StubSessionRepository:
        def get_session(self, session_id: str) -> Session | None:
            del session_id
            return None

    class StubCapabilityFacade:
        def build_prompt_fragments(self, **_: object) -> dict[str, str]:
            return {
                "inventory_summary": "Loaded skills inventory:\n- agent-browser",
                "schema_summary": "Capability schema summary.",
                "prompt_fragment": "Capability prompt fragment.",
            }

    engine = WorkflowLoopEngine(
        executor=Executor(),
        reflector=Reflector(),
        max_active_execution_records=10,
        max_active_messages=10,
        session_repository=cast(Any, StubSessionRepository()),
        run_log_repository=cast(Any, _StubRunLogRepository()),
        graph_repository=cast(Any, object()),
        capability_facade=cast(Any, StubCapabilityFacade()),
    )

    run = WorkflowRun(
        session_id="session-workspace-state",
        template_name="authorized-assessment",
        status=WorkflowRunStatus.RUNNING,
        current_stage="context_collect",
        state_json={
            "goal": "Need compact workspace continuity",
            "runtime_policy": {},
            "messages": [
                {"role": "user", "content": "Force compact for workspace continuity."},
                {"role": "assistant", "content": "Preparing compact boundary context."},
            ],
            "execution_records": [
                {
                    "id": "trace-old",
                    "task_name": "context_collect.attack_surface",
                    "task_node_id": "task-old",
                    "summary": "Mapped exposed services.",
                    "status": "completed",
                    "command_or_action": "execute:context_collect.attack_surface",
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
                    "resume_condition": "provide answer",
                    "task_id": "pending-task",
                    "task_name": "scope_clarification",
                }
            },
            "retrieval_manifest": {
                "project": {
                    "sources": [
                        {"source_id": "memory-one", "scope": "project"},
                        {"source_id": "memory-two", "scope": "project"},
                    ]
                }
            },
        },
    )
    task = TaskNode(
        workflow_run_id=run.id,
        name="scope_clarification",
        node_type=TaskNodeType.TASK,
        status=TaskNodeStatus.READY,
        sequence=1,
        metadata_json={
            "stage_key": "context_collect",
            "workflow_tool": "workflow.ask_user_question",
            "question": "Which host should the agent validate first?",
            "description": "Ask for the first host to validate.",
        },
    )

    blocked_result = asyncio.run(
        engine.advance(
            run=run,
            tasks=[task],
            approve=False,
            user_input=None,
            resume_token=None,
            resolution_payload=None,
        )
    )

    blocked_state = blocked_result.state
    compact_runtime = cast(
        dict[str, Any], cast(dict[str, Any], blocked_state["context"])["compact_runtime"]
    )
    retained_live_state = cast(dict[str, Any], compact_runtime["retained_live_state"])
    workspace_state = cast(dict[str, Any], retained_live_state["workspace_state"])
    assert workspace_state["active_stage"] == "context_collect"
    assert workspace_state["active_tasks"]
    assert cast(dict[str, Any], workspace_state["pending_protocol"])["kind"] == "interaction"
    assert workspace_state["active_capability_inventory_summary"]
    assert isinstance(workspace_state["recent_transcript_highlights"], list)
    assert workspace_state["selected_project_memory_entries"] == ["memory-one", "memory-two"]

    continuity = cast(dict[str, Any], cast(dict[str, Any], blocked_state["context"])["prompting"])[
        "continuity"
    ]
    assert cast(dict[str, Any], continuity["workspace_state"])["active_stage"] == "context_collect"
    workspace_rehydrate = cast(dict[str, Any], continuity["workspace_rehydrate"])
    assert cast(dict[str, Any], workspace_rehydrate["state"])["active_stage"] == "context_collect"
    assert "boundary" in cast(dict[str, Any], workspace_rehydrate["provenance"])["used_sources"]
    assistant_turn_input = cast(
        dict[str, Any], cast(dict[str, Any], blocked_state["assistant_turn"])["input"]
    )
    assert (
        cast(dict[str, Any], assistant_turn_input["transcript_context"])["workspace_state"][
            "active_stage"
        ]
        == "context_collect"
    )
    assert cast(dict[str, Any], assistant_turn_input["reasoning_frame"])["workspace_state"][
        "selected_project_memory_entries"
    ] == ["memory-one", "memory-two"]


def test_workflow_execution_records_and_graph_snapshots_are_persisted(client: TestClient) -> None:
    session_id = _create_session(
        client, goal="Complete authorized workflow and emit graph snapshots"
    )
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    final_payload: dict[str, Any] | None = None
    for _ in range(30):
        response = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
        assert response.status_code == 200
        final_payload = cast(dict[str, Any], api_data(response))
        if final_payload["status"] == "done":
            break

    assert final_payload is not None
    assert final_payload["status"] == "done"

    state = cast(dict[str, Any], final_payload["state"])
    execution_records = cast(list[dict[str, Any]], state["execution_records"])
    assert execution_records
    assert all(
        isinstance(record.get("id"), str) and record["id"].startswith("trace-")
        for record in execution_records
    )
    capability_record = next(
        (
            record
            for record in execution_records
            if record.get("command_or_action") == "execute:skill_mcp_sync.capability_snapshot"
        ),
        None,
    )
    assert capability_record is not None
    output_json = cast(dict[str, Any], capability_record["output_json"])
    assert isinstance(output_json.get("capability_snapshot"), dict)
    runtime_protocol = cast(dict[str, Any], output_json["runtime_protocol"])
    assert runtime_protocol["version"] == "2.0"
    assert runtime_protocol["tool_name"] == "workflow.capability_snapshot"

    task_graph = client.get(f"/api/sessions/{session_id}/graphs/task")
    evidence_graph = client.get(f"/api/sessions/{session_id}/graphs/evidence")
    causal_graph = client.get(f"/api/sessions/{session_id}/graphs/causal")
    attack_graph = client.get(f"/api/sessions/{session_id}/graphs/attack")

    assert task_graph.status_code == 200
    assert evidence_graph.status_code == 200
    assert causal_graph.status_code == 200
    assert attack_graph.status_code == 200

    task_payload = cast(dict[str, Any], api_data(task_graph))
    evidence_payload = cast(dict[str, Any], api_data(evidence_graph))
    causal_payload = cast(dict[str, Any], api_data(causal_graph))
    attack_payload = cast(dict[str, Any], api_data(attack_graph))

    assert task_payload["graph_type"] == "task"
    assert evidence_payload["graph_type"] == "evidence"
    assert causal_payload["graph_type"] == "causal"
    assert attack_payload["graph_type"] == "attack"
    assert len(cast(list[Any], evidence_payload["nodes"])) > 0
    assert len(cast(list[Any], causal_payload["nodes"])) > 0
    assert len(cast(list[Any], attack_payload["nodes"])) > 0


def test_workflow_persists_typed_loop_cycle_artifacts_without_breaking_batch_state(
    client: TestClient,
) -> None:
    session_id = _create_session(client, goal="Persist typed loop cycle state")
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
    assert advance.status_code == 200
    payload = cast(dict[str, Any], api_data(advance))
    state = cast(dict[str, Any], payload["state"])
    batch_state = cast(dict[str, Any], state["batch"])
    loop_state = cast(dict[str, Any], state["loop"])
    cycles = cast(list[dict[str, Any]], loop_state["cycles"])

    assert cycles
    latest_cycle = cycles[-1]
    assert isinstance(latest_cycle["cycle_id"], str) and latest_cycle["cycle_id"]
    assert latest_cycle["batch_cycle"] == batch_state["cycle"]
    assert isinstance(latest_cycle["selected_tasks"], list)
    assert latest_cycle["scheduler_mode"] == "phase3_read_parallel_write_serial"
    assert isinstance(latest_cycle["parallel_read_group"], list)
    assert isinstance(latest_cycle["serialized_write_group"], list)
    assert isinstance(latest_cycle["scheduler_summary"], dict)
    assert isinstance(latest_cycle["merge_summary"], dict)
    assert isinstance(latest_cycle["partial_failures"], list)
    assert isinstance(latest_cycle["retrieval_summary"], str)
    assert isinstance(latest_cycle["tool_results"], list)
    assert isinstance(latest_cycle["reflection_summary"], str)
    assert isinstance(latest_cycle["memory_writes"], list)
    assert isinstance(latest_cycle["compaction_summary"], dict)
    assert isinstance(latest_cycle["assistant_turn_input"], dict)
    assert isinstance(latest_cycle["assistant_turn_plan"], dict)
    assert isinstance(latest_cycle["assistant_turn_outcome"], dict)
    assert latest_cycle["next_action"] in {"continue", "complete", "await_approval", "idle"}
    scheduler_summary = cast(dict[str, Any], latest_cycle["scheduler_summary"])
    assert scheduler_summary["mode"] == "phase3_read_parallel_write_serial"
    assert scheduler_summary["selected_task_ids"] == [
        task["task_id"] for task in cast(list[dict[str, Any]], latest_cycle["selected_tasks"])
    ]
    merge_summary = cast(dict[str, Any], latest_cycle["merge_summary"])
    assert isinstance(merge_summary.get("phases"), list)
    assert isinstance(merge_summary.get("merged_task_ids"), list)
    assert isinstance(merge_summary.get("partial_failure_count"), int)

    assistant_turn_input = cast(dict[str, Any], latest_cycle["assistant_turn_input"])
    assistant_turn_plan = cast(dict[str, Any], latest_cycle["assistant_turn_plan"])
    assistant_turn_outcome = cast(dict[str, Any], latest_cycle["assistant_turn_outcome"])
    assert isinstance(assistant_turn_input["turn_id"], str) and assistant_turn_input["turn_id"]
    assert assistant_turn_input["cycle_id"] == latest_cycle["cycle_id"]
    assert assistant_turn_input["current_goal"] == state["goal"]
    assert isinstance(assistant_turn_input["active_tasks"], list)
    assert assistant_turn_input["active_tasks"]
    assert isinstance(assistant_turn_input["retrieval_context"], dict)
    assert isinstance(assistant_turn_input["memory_context"], dict)
    assert isinstance(assistant_turn_input["transcript_context"], dict)
    assert isinstance(assistant_turn_input["reasoning_frame"], dict)
    assert (
        cast(dict[str, Any], assistant_turn_input["retrieval_context"])["summary"]
        == latest_cycle["retrieval_summary"]
    )
    assert (
        cast(dict[str, Any], assistant_turn_input["memory_context"])["summary"]
        == cast(dict[str, Any], latest_cycle["memory"])["summary"]
    )
    assert cast(dict[str, Any], assistant_turn_input["transcript_context"])["source"] == (
        "runtime_transcript"
    )
    assert cast(dict[str, Any], assistant_turn_input["reasoning_frame"])["last_directive"] in {
        "continue",
        "retry_same_wave",
        "replan_subgraph",
        "await_user_input",
        "await_approval",
        "finalize",
        "stop_loop",
    }
    assert isinstance(assistant_turn_plan["turn_id"], str)
    assert assistant_turn_plan["turn_id"] == assistant_turn_input["turn_id"]
    assert assistant_turn_plan["cycle_id"] == latest_cycle["cycle_id"]
    assert isinstance(assistant_turn_plan["recommended_tool_wave"], dict)
    recommended_tool_wave = cast(dict[str, Any], assistant_turn_plan["recommended_tool_wave"])
    assert recommended_tool_wave["scheduler_mode"] == latest_cycle["scheduler_mode"]
    assert recommended_tool_wave["expected_task_ids"] == [
        task["task_id"] for task in cast(list[dict[str, Any]], latest_cycle["selected_tasks"])
    ]
    assert recommended_tool_wave["parallel_read_task_ids"] == latest_cycle["parallel_read_group"]
    assert (
        recommended_tool_wave["serialized_write_task_ids"] == latest_cycle["serialized_write_group"]
    )
    assert isinstance(assistant_turn_outcome["turn_id"], str)
    assert assistant_turn_outcome["turn_id"] == assistant_turn_input["turn_id"]
    assert assistant_turn_outcome["cycle_id"] == latest_cycle["cycle_id"]
    assert assistant_turn_outcome["resulting_directive"] in {
        "continue",
        "retry_same_wave",
        "replan_subgraph",
        "await_user_input",
        "await_approval",
        "finalize",
        "stop_loop",
    }
    assert isinstance(assistant_turn_outcome["next_turn_hint"], str)
    assert isinstance(assistant_turn_outcome["unresolved_questions"], list)
    assert assistant_turn_outcome["next_action"] == latest_cycle["next_action"]


def test_workflow_builds_retrieval_memory_and_projection_context_for_cycle_and_tool_inputs(
    client: TestClient,
) -> None:
    session_id = _create_session(client, goal="Build workflow context projections")
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
    assert advance.status_code == 200
    payload = cast(dict[str, Any], api_data(advance))
    state = cast(dict[str, Any], payload["state"])
    context = cast(dict[str, Any], state["context"])
    retrieval_manifest = cast(dict[str, Any], state["retrieval_manifest"])
    retrieval = cast(dict[str, Any], context["retrieval"])
    memory = cast(dict[str, Any], context["memory"])
    projection = cast(dict[str, Any], context["projection"])
    prompting = cast(dict[str, Any], context["prompting"])
    compact_runtime = cast(dict[str, Any], context["compact_runtime"])

    session_local_pack = cast(dict[str, Any], retrieval["session_local"])
    project_pack = cast(dict[str, Any], retrieval["project"])
    capability_pack = cast(dict[str, Any], retrieval["capability"])
    assert session_local_pack["scope"] == "session_local"
    assert session_local_pack["status"] in {"ready", "empty"}
    assert project_pack["scope"] == "project"
    assert capability_pack["scope"] == "capability"

    working_memory = cast(dict[str, Any], memory["working"])
    session_memory = cast(dict[str, Any], memory["session"])
    project_memory = cast(dict[str, Any], memory["project"])
    assert working_memory["layer"] == "working"
    assert session_memory["layer"] == "session"
    assert project_memory["layer"] == "project"
    assert isinstance(memory["promotions"], list)
    assert isinstance(memory["demotions"], list)

    levels = cast(list[dict[str, Any]], projection["levels"])
    assert len(levels) == 5
    assert [level["level"] for level in levels] == [1, 2, 3, 4, 5]
    assert projection["active_level"] in {1, 2, 3, 4, 5}
    assert isinstance(prompting["fragments"], list)
    assert isinstance(prompting["budget"], dict)
    assert isinstance(prompting["continuity"], dict)
    assert isinstance(compact_runtime, dict)
    assert isinstance(compact_runtime["metrics"], dict)
    assert isinstance(compact_runtime["thresholds"], dict)
    assert cast(dict[str, Any], retrieval_manifest["policy"])["already_surfaced_penalty"] >= 0
    assert cast(dict[str, Any], retrieval_manifest["session_local"])["scope"] == "session_derived"
    assert cast(dict[str, Any], retrieval_manifest["project"])["scope"] == "project"
    assert cast(dict[str, Any], retrieval_manifest["capability"])["scope"] == "capability_adjacent"
    assert isinstance(cast(dict[str, Any], prompting["continuity"])["workspace_state"], dict)
    assert isinstance(cast(dict[str, Any], prompting["continuity"])["workspace_rehydrate"], dict)

    loop_state = cast(dict[str, Any], state["loop"])
    latest_cycle = cast(list[dict[str, Any]], loop_state["cycles"])[-1]
    assert isinstance(latest_cycle["retrieval"], dict)
    assert isinstance(latest_cycle["memory"], dict)
    assert isinstance(latest_cycle["context_projection"], dict)
    assert isinstance(cast(dict[str, Any], latest_cycle["compaction_summary"])["runtime"], dict)
    assert cast(dict[str, Any], latest_cycle["context_projection"])["active_level"] in {
        1,
        2,
        3,
        4,
        5,
    }

    execution_records = cast(list[dict[str, Any]], state["execution_records"])
    assert execution_records
    first_input = cast(dict[str, Any], execution_records[0]["input_json"])
    assert isinstance(first_input["retrieval"], dict)
    assert isinstance(first_input["memory"], dict)
    assert isinstance(first_input["context_projection"], dict)
    assert isinstance(first_input["prompting"], dict)


def test_workflow_runtime_transcript_is_append_only_and_execution_records_are_projection_views(
    client: TestClient,
) -> None:
    session_id = _create_session(client, goal="Drive workflow from transcript runtime state")
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    first_advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
    assert first_advance.status_code == 200
    first_payload = cast(dict[str, Any], api_data(first_advance))
    first_state = cast(dict[str, Any], first_payload["state"])
    first_runtime = cast(dict[str, Any], first_state["runtime_transcript"])

    assert set(first_runtime.keys()) == {
        "turns",
        "deltas",
        "tool_use_records",
        "tool_result_records",
        "compact_events",
        "reinjection_events",
        "last_directive",
    }
    assert cast(list[dict[str, Any]], first_runtime["turns"])
    assert cast(list[dict[str, Any]], first_runtime["deltas"])
    assert cast(list[dict[str, Any]], first_runtime["tool_use_records"])
    assert cast(list[dict[str, Any]], first_runtime["tool_result_records"])
    first_delta_ids = [
        delta["delta_id"] for delta in cast(list[dict[str, Any]], first_runtime["deltas"])
    ]

    first_execution_record = cast(list[dict[str, Any]], first_state["execution_records"])[0]
    assert first_execution_record["transcript_delta_id"] in first_delta_ids
    first_delta = next(
        delta
        for delta in cast(list[dict[str, Any]], first_runtime["deltas"])
        if delta["delta_id"] == first_execution_record["transcript_delta_id"]
    )
    assert cast(list[dict[str, Any]], first_delta["tool_use_blocks"])
    assert cast(list[dict[str, Any]], first_delta["tool_result_blocks"]) or cast(
        list[dict[str, Any]], first_delta["tool_error_blocks"]
    )
    assert first_runtime["last_directive"] in {
        "continue",
        "retry_same_wave",
        "replan_subgraph",
        "await_user_input",
        "await_approval",
        "finalize",
        "stop_loop",
    }

    second_advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
    assert second_advance.status_code == 200
    second_payload = cast(dict[str, Any], api_data(second_advance))
    second_state = cast(dict[str, Any], second_payload["state"])
    second_runtime = cast(dict[str, Any], second_state["runtime_transcript"])
    second_delta_ids = [
        delta["delta_id"] for delta in cast(list[dict[str, Any]], second_runtime["deltas"])
    ]

    assert len(second_delta_ids) > len(first_delta_ids)
    assert all(delta_id in second_delta_ids for delta_id in first_delta_ids)
    assert cast(list[dict[str, Any]], second_runtime["reinjection_events"])


def test_workflow_context_consumers_prefer_runtime_transcript_continuity(
    client: TestClient,
) -> None:
    session_id = _create_session(client, goal="Use transcript continuity in retrieval and memory")
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
    assert advance.status_code == 200
    payload = cast(dict[str, Any], api_data(advance))
    state = cast(dict[str, Any], payload["state"])
    context = cast(dict[str, Any], state["context"])
    retrieval = cast(dict[str, Any], context["retrieval"])
    memory = cast(dict[str, Any], context["memory"])
    prompting = cast(dict[str, Any], context["prompting"])
    runtime_transcript = cast(dict[str, Any], state["runtime_transcript"])

    session_local_items = cast(
        list[dict[str, Any]], cast(dict[str, Any], retrieval["session_local"])["items"]
    )
    assert any(item["kind"] == "transcript_delta" for item in session_local_items)

    working_raw_entries = cast(
        list[dict[str, Any]], cast(dict[str, Any], memory["working"])["raw_entries"]
    )
    assert any(
        cast(list[dict[str, Any]], entry["citations"])
        and cast(list[dict[str, Any]], entry["citations"])[0]["source_kind"]
        == "transcript_tool_result"
        for entry in working_raw_entries
    )

    continuity = cast(dict[str, Any], prompting["continuity"])
    assert continuity["source"] == "runtime_transcript"
    assert cast(list[str], continuity["recent_delta_ids"])
    assert cast(list[str], continuity["tool_result_delta_ids"])
    assert cast(list[dict[str, Any]], runtime_transcript["reinjection_events"])
    assert cast(list[dict[str, Any]], runtime_transcript["compact_events"]) or cast(
        list[dict[str, Any]], runtime_transcript["deltas"]
    )

    assistant_turn_state = cast(dict[str, Any], state["assistant_turn"])
    assistant_turn_input = cast(dict[str, Any], assistant_turn_state["input"])
    assistant_turn_plan = cast(dict[str, Any], assistant_turn_state["plan"])
    assistant_turn_outcome = cast(dict[str, Any], assistant_turn_state["outcome"])
    assistant_turn_history = cast(list[dict[str, Any]], assistant_turn_state["history"])
    assert assistant_turn_input["turn_id"] == assistant_turn_plan["turn_id"]
    assert assistant_turn_input["turn_id"] == assistant_turn_outcome["turn_id"]
    assert isinstance(assistant_turn_input["active_tasks"], list)
    assert isinstance(assistant_turn_input["retrieval_context"], dict)
    assert isinstance(assistant_turn_input["memory_context"], dict)
    assert isinstance(assistant_turn_input["transcript_context"], dict)
    assert isinstance(assistant_turn_plan["recommended_tool_wave"], dict)
    assert isinstance(assistant_turn_outcome["next_turn_hint"], str)
    assert isinstance(assistant_turn_outcome["unresolved_questions"], list)
    assert assistant_turn_history
    assert (
        assistant_turn_history[-1]["outcome"]["resulting_directive"]
        == assistant_turn_outcome["resulting_directive"]
    )
    assert (
        continuity["assistant_turn_carry_forward"]
        == assistant_turn_outcome["carry_forward_context"]
    )
    assert (
        continuity["assistant_turn_next_directive"] == assistant_turn_outcome["resulting_directive"]
    )
    assert continuity["assistant_turn_next_hint"] == assistant_turn_outcome["next_turn_hint"]
    assert isinstance(continuity["workspace_state"], dict)


def test_context_additions_preserve_export_replay_and_session_history_compatibility(
    client: TestClient,
) -> None:
    session_id = _create_session(
        client, goal="Preserve export replay session history compatibility"
    )
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    for _ in range(2):
        advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
        assert advance.status_code == 200

    export_response = client.get(f"/api/workflows/{run_id}/export")
    assert export_response.status_code == 200
    export_payload = cast(dict[str, Any], api_data(export_response))
    assert set(export_payload.keys()) == {
        "run",
        "task_graph",
        "evidence_graph",
        "causal_graph",
        "attack_graph",
        "execution_records",
        "replan_records",
        "batch_state",
    }

    replay_response = client.get(f"/api/workflows/{run_id}/replay")
    assert replay_response.status_code == 200
    replay_payload = cast(dict[str, Any], api_data(replay_response))
    replay_step = cast(list[dict[str, Any]], replay_payload["replay_steps"])[0]
    assert set(replay_step.keys()) == {
        "index",
        "trace_id",
        "task_node_id",
        "task_name",
        "status",
        "started_at",
        "ended_at",
        "summary",
        "evidence_confidence",
        "retry_attempt",
        "batch_cycle",
    }

    history_response = client.get(
        f"/api/sessions/{session_id}/history",
        params={"event_type": "workflow.execution.recorded", "sort_order": "asc"},
    )
    assert history_response.status_code == 200
    history_entries = cast(list[dict[str, Any]], api_data(history_response))
    assert history_entries
    assert all(entry["source"] == "workflow.executor" for entry in history_entries)


def test_start_workflow_publishes_graph_events_for_task_evidence_and_causal(
    client: TestClient,
) -> None:
    session_id = _create_session(client)

    with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
        response = client.post(
            "/api/workflows/authorized-assessment/start",
            json={"session_id": session_id},
        )

        assert response.status_code == 201
        events = [websocket.receive_json() for _ in range(44)]

    event_types = [cast(str, event["type"]) for event in events]
    assert event_types[0] == "workflow.run.started"
    assert event_types[1] == "workflow.stage.changed"
    assert event_types.count("workflow.task.updated") == 19
    assert event_types.count("task.planned") == 19
    assert event_types.count("graph.updated") == 4


def test_advance_workflow_publishes_started_and_finished_events_for_each_batch_task(
    client: TestClient,
) -> None:
    session_id = _create_session(client)
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    first_advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
    assert first_advance.status_code == 200

    with client.websocket_connect(f"/api/sessions/{session_id}/events") as websocket:
        second_advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
        assert second_advance.status_code == 200
        second_payload = cast(dict[str, Any], api_data(second_advance))
        batch_state = cast(dict[str, Any], cast(dict[str, Any], second_payload["state"])["batch"])
        executed_task_ids = cast(list[str], batch_state["executed_task_ids"])
        assert len(executed_task_ids) == 2

        event_count = (
            1
            + len(cast(list[dict[str, Any]], second_payload["tasks"]))
            + (2 * len(executed_task_ids))
            + 4
        )
        events = [websocket.receive_json() for _ in range(event_count)]

    event_types = [cast(str, event["type"]) for event in events]
    assert event_types.count("workflow.stage.changed") == 1
    assert event_types.count("workflow.task.updated") == 19
    assert event_types.count("task.started") == 2
    assert event_types.count("task.finished") == 2
    assert event_types.count("graph.updated") == 4


def test_batch_execution_logs_all_records_to_session_history(client: TestClient) -> None:
    session_id = _create_session(client, goal="Record all batch executions in session history")
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    first_advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
    assert first_advance.status_code == 200

    second_advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
    assert second_advance.status_code == 200
    second_payload = cast(dict[str, Any], api_data(second_advance))
    batch_state = cast(dict[str, Any], cast(dict[str, Any], second_payload["state"])["batch"])
    assert len(cast(list[str], batch_state["executed_task_ids"])) == 2

    history_response = client.get(
        f"/api/sessions/{session_id}/history",
        params={"event_type": "workflow.execution.recorded", "sort_order": "asc"},
    )
    assert history_response.status_code == 200
    history_entries = cast(list[dict[str, Any]], api_data(history_response))
    assert len(history_entries) == 3
    assert all(entry["source"] == "workflow.executor" for entry in history_entries)
    assert all(cast(dict[str, Any], entry["payload"]).get("trace_id") for entry in history_entries)


def test_workflow_executes_read_tasks_with_async_parallelism_and_merges_state_in_selection_order(
    monkeypatch: Any,
) -> None:
    import asyncio
    import threading

    to_thread_calls: list[str] = []
    real_to_thread = asyncio.to_thread
    barrier = threading.Barrier(2)

    class StubExecutor:
        def execute(self, *, context: object, task: Any) -> ExecutionResult:
            del context
            from datetime import UTC, datetime

            barrier.wait(timeout=1.0)
            now = datetime.now(UTC)
            return ExecutionResult(
                trace_id=f"trace-{task.name}",
                source_type="runtime",
                source_name="test-executor",
                command_or_action=f"execute:{task.name}",
                input_payload={"task": task.name},
                output_payload={"status": "completed", "artifacts": [], "citations": []},
                status=TaskNodeStatus.COMPLETED,
                started_at=now,
                ended_at=now,
            )

        def resolve_tool_spec(self, task: TaskNode) -> ToolSpec:
            del task
            return ToolSpec(
                name="workflow.capability_snapshot",
                category=ToolCategory.DISCOVERY,
                capability=ToolCapability.CAPABILITY_SNAPSHOT,
                safety_profile=ToolSafetyProfile(
                    writes_state=False,
                    is_concurrency_safe=True,
                    is_read_only=True,
                    is_destructive=False,
                ),
                access_mode=ToolAccessMode.READ,
            )

    class StubSessionRepository:
        def get_session(self, session_id: str) -> Session | None:
            del session_id
            return None

    class StubCapabilityFacade:
        def build_prompt_fragments(self, **_: object) -> dict[str, str]:
            return {
                "inventory_summary": "",
                "schema_summary": "",
                "prompt_fragment": "",
            }

    engine = WorkflowLoopEngine(
        executor=cast(Any, StubExecutor()),
        reflector=Reflector(),
        max_active_execution_records=10,
        max_active_messages=10,
        session_repository=cast(Any, StubSessionRepository()),
        run_log_repository=cast(Any, _StubRunLogRepository()),
        graph_repository=cast(Any, object()),
        capability_facade=cast(Any, StubCapabilityFacade()),
    )

    run = WorkflowRun(
        session_id="session-1",
        template_name="authorized-assessment",
        status=WorkflowRunStatus.RUNNING,
        current_stage="discovery",
        state_json={"goal": "parallel read test", "runtime_policy": {}},
    )
    session = Session(project_id="project-1")
    read_tasks = [
        TaskNode(
            workflow_run_id=run.id,
            name="read_alpha",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.READY,
            sequence=1,
            metadata_json={"stage_key": "discovery", "scheduler_access_mode": "read"},
        ),
        TaskNode(
            workflow_run_id=run.id,
            name="read_beta",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.READY,
            sequence=2,
            metadata_json={"stage_key": "discovery", "scheduler_access_mode": "read"},
        ),
    ]

    async def recording_to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        task = kwargs.get("task")
        if task is not None and hasattr(task, "name"):
            to_thread_calls.append(cast(str, task.name))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr("app.agent.loop_engine.asyncio.to_thread", recording_to_thread)

    outcomes = asyncio.run(
        engine._execute_parallel_read_wave(
            run=run,
            session=session,
            mutable_state={"goal": "parallel read test", "runtime_policy": {}},
            context_snapshot=ContextSnapshot.empty(),
            read_tasks=read_tasks,
        )
    )

    assert [outcome.task.name for outcome in outcomes] == ["read_alpha", "read_beta"]
    assert [outcome.execution.status for outcome in outcomes] == [
        TaskNodeStatus.COMPLETED,
        TaskNodeStatus.COMPLETED,
    ]
    assert to_thread_calls == ["read_alpha", "read_beta"]


def test_parallel_read_wave_does_not_mutate_state_before_merge_and_merges_after_barrier() -> None:
    import asyncio

    class StubExecutor:
        def execute(self, *, context: object, task: Any) -> ExecutionResult:
            del context
            from datetime import UTC, datetime

            now = datetime.now(UTC)
            return ExecutionResult(
                trace_id=f"trace-{task.name}",
                source_type="runtime",
                source_name="test-executor",
                command_or_action=f"execute:{task.name}",
                input_payload={"task": task.name},
                output_payload={
                    "status": "completed",
                    "artifacts": [{"name": f"{task.name}.json"}],
                    "citations": [{"label": task.name}],
                },
                status=TaskNodeStatus.COMPLETED,
                started_at=now,
                ended_at=now,
            )

        def resolve_tool_spec(self, task: TaskNode) -> ToolSpec:
            del task
            return ToolSpec(
                name="workflow.capability_snapshot",
                category=ToolCategory.DISCOVERY,
                capability=ToolCapability.CAPABILITY_SNAPSHOT,
                safety_profile=ToolSafetyProfile(
                    writes_state=False,
                    is_concurrency_safe=True,
                    is_read_only=True,
                    is_destructive=False,
                ),
                access_mode=ToolAccessMode.READ,
            )

    class StubSessionRepository:
        def get_session(self, session_id: str) -> Session | None:
            del session_id
            return None

    class StubCapabilityFacade:
        def build_prompt_fragments(self, **_: object) -> dict[str, str]:
            return {
                "inventory_summary": "",
                "schema_summary": "",
                "prompt_fragment": "",
            }

    engine = WorkflowLoopEngine(
        executor=cast(Any, StubExecutor()),
        reflector=Reflector(),
        max_active_execution_records=10,
        max_active_messages=10,
        session_repository=cast(Any, StubSessionRepository()),
        run_log_repository=cast(Any, _StubRunLogRepository()),
        graph_repository=cast(Any, object()),
        capability_facade=cast(Any, StubCapabilityFacade()),
    )

    run = WorkflowRun(
        session_id="session-1",
        template_name="authorized-assessment",
        status=WorkflowRunStatus.RUNNING,
        current_stage="discovery",
        state_json={"goal": "merge barrier test", "runtime_policy": {}},
    )
    session = Session(project_id="project-1")
    mutable_state: dict[str, Any] = {"goal": "merge barrier test", "runtime_policy": {}}
    read_tasks = [
        TaskNode(
            workflow_run_id=run.id,
            name="read_alpha",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.READY,
            sequence=1,
            metadata_json={"stage_key": "discovery", "scheduler_access_mode": "read"},
        ),
        TaskNode(
            workflow_run_id=run.id,
            name="read_beta",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.READY,
            sequence=2,
            metadata_json={"stage_key": "discovery", "scheduler_access_mode": "read"},
        ),
    ]

    outcomes = asyncio.run(
        engine._execute_parallel_read_wave(
            run=run,
            session=session,
            mutable_state=mutable_state,
            context_snapshot=ContextSnapshot.empty(),
            read_tasks=read_tasks,
        )
    )

    assert len(outcomes) == 2
    assert "execution_records" not in mutable_state
    assert "findings" not in mutable_state
    assert "graph_updates" not in mutable_state

    tool_results: list[dict[str, Any]] = []
    reflection_summaries: list[str] = []
    executed_task_ids: list[str] = []
    merge_result = engine._merge_execution_outcomes(
        run=run,
        outcomes=outcomes,
        mutable_state=mutable_state,
        tasks=read_tasks,
        tool_results=tool_results,
        reflection_summaries=reflection_summaries,
        executed_task_ids=executed_task_ids,
        scheduler_group="parallel_read_group",
    )

    assert merge_result["merged_after_batch_completion"] is True
    assert merge_result["merged_task_ids"] == [task.id for task in read_tasks]
    assert [record["task_node_id"] for record in mutable_state["execution_records"]] == [
        task.id for task in read_tasks
    ]
    assert [result["task_name"] for result in tool_results] == ["read_alpha", "read_beta"]


def test_serial_write_tasks_execute_in_order_without_parallel_worker_path(
    monkeypatch: Any,
) -> None:
    import asyncio

    executed_names: list[str] = []

    class StubExecutor:
        def execute(self, *, context: object, task: Any) -> ExecutionResult:
            del context
            from datetime import UTC, datetime

            executed_names.append(cast(str, task.name))
            now = datetime.now(UTC)
            return ExecutionResult(
                trace_id=f"trace-{task.name}",
                source_type="runtime",
                source_name="test-executor",
                command_or_action=f"execute:{task.name}",
                input_payload={"task": task.name},
                output_payload={"status": "completed", "artifacts": [], "citations": []},
                status=TaskNodeStatus.COMPLETED,
                started_at=now,
                ended_at=now,
            )

        def resolve_tool_spec(self, task: TaskNode) -> ToolSpec:
            del task
            return ToolSpec(
                name="workflow.structured_runtime",
                category=ToolCategory.EXECUTION,
                capability=ToolCapability.STRUCTURED_RUNTIME,
                safety_profile=ToolSafetyProfile(
                    writes_state=True,
                    is_concurrency_safe=False,
                    is_read_only=False,
                    is_destructive=True,
                ),
                access_mode=ToolAccessMode.WRITE,
            )

    class StubSessionRepository:
        def get_session(self, session_id: str) -> Session | None:
            del session_id
            return None

    class StubCapabilityFacade:
        def build_prompt_fragments(self, **_: object) -> dict[str, str]:
            return {
                "inventory_summary": "",
                "schema_summary": "",
                "prompt_fragment": "",
            }

    async def fail_to_thread(func: Any, /, *args: Any, **kwargs: Any) -> Any:
        del func, args, kwargs
        raise AssertionError("serialized write execution should not use asyncio.to_thread")

    monkeypatch.setattr("app.agent.loop_engine.asyncio.to_thread", fail_to_thread)

    engine = WorkflowLoopEngine(
        executor=cast(Any, StubExecutor()),
        reflector=Reflector(),
        max_active_execution_records=10,
        max_active_messages=10,
        session_repository=cast(Any, StubSessionRepository()),
        run_log_repository=cast(Any, _StubRunLogRepository()),
        graph_repository=cast(Any, object()),
        capability_facade=cast(Any, StubCapabilityFacade()),
    )

    run = WorkflowRun(
        session_id="session-1",
        template_name="authorized-assessment",
        status=WorkflowRunStatus.RUNNING,
        current_stage="validation",
        state_json={
            "goal": "serial write test",
            "runtime_policy": {},
            "batch": {"max_nodes_per_cycle": 2},
        },
    )
    write_tasks = [
        TaskNode(
            workflow_run_id=run.id,
            name="write_alpha",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.READY,
            sequence=1,
            metadata_json={"stage_key": "validation", "scheduler_access_mode": "write"},
        ),
        TaskNode(
            workflow_run_id=run.id,
            name="write_beta",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.READY,
            sequence=2,
            metadata_json={"stage_key": "validation", "scheduler_access_mode": "write"},
        ),
    ]

    result = asyncio.run(
        engine.advance(
            run=run,
            tasks=write_tasks,
            approve=True,
            user_input=None,
            resume_token=None,
            resolution_payload=None,
        )
    )
    state = cast(dict[str, Any], result.state)

    assert executed_names == ["write_alpha", "write_beta"]
    assert result.executed_task_ids == [task.id for task in write_tasks]
    assert [
        record["task_node_id"] for record in cast(list[dict[str, Any]], state["execution_records"])
    ] == [task.id for task in write_tasks]


def test_workflow_preserves_blocked_tool_execution_as_blocked_runtime_state() -> None:
    import asyncio

    class StubExecutor:
        def execute(self, *, context: object, task: Any) -> ExecutionResult:
            del context
            from datetime import UTC, datetime

            now = datetime.now(UTC)
            return ExecutionResult(
                trace_id=f"trace-{task.name}",
                source_type="runtime",
                source_name="test-executor",
                command_or_action=f"execute:{task.name}",
                input_payload={"task": task.name},
                output_payload={
                    "stdout": "",
                    "stderr": "tool execution blocked pending user interaction",
                    "exit_code": 1,
                    "execution_blocked": True,
                    "interaction_required": True,
                    "interrupt_behavior": "user_interaction",
                    "block_reason": "user_interaction_required",
                },
                status=TaskNodeStatus.BLOCKED,
                started_at=now,
                ended_at=now,
            )

        def resolve_tool_spec(self, task: TaskNode) -> ToolSpec:
            del task
            return ToolSpec(
                name="workflow.structured_runtime",
                category=ToolCategory.EXECUTION,
                capability=ToolCapability.STRUCTURED_RUNTIME,
                safety_profile=ToolSafetyProfile(
                    writes_state=True,
                    is_concurrency_safe=False,
                    is_read_only=False,
                    is_destructive=True,
                ),
                access_mode=ToolAccessMode.WRITE,
            )

    class StubSessionRepository:
        def get_session(self, session_id: str) -> Session | None:
            del session_id
            return None

    class StubCapabilityFacade:
        def build_prompt_fragments(self, **_: object) -> dict[str, str]:
            return {
                "inventory_summary": "",
                "schema_summary": "",
                "prompt_fragment": "",
            }

    engine = WorkflowLoopEngine(
        executor=cast(Any, StubExecutor()),
        reflector=Reflector(),
        max_active_execution_records=10,
        max_active_messages=10,
        session_repository=cast(Any, StubSessionRepository()),
        run_log_repository=cast(Any, _StubRunLogRepository()),
        graph_repository=cast(Any, object()),
        capability_facade=cast(Any, StubCapabilityFacade()),
    )

    run = WorkflowRun(
        session_id="session-1",
        template_name="authorized-assessment",
        status=WorkflowRunStatus.RUNNING,
        current_stage="validation",
        state_json={
            "goal": "blocked runtime test",
            "runtime_policy": {},
            "batch": {"max_nodes_per_cycle": 1},
        },
    )
    blocked_task = TaskNode(
        workflow_run_id=run.id,
        name="needs.input",
        node_type=TaskNodeType.TASK,
        status=TaskNodeStatus.READY,
        sequence=1,
        metadata_json={"stage_key": "validation", "scheduler_access_mode": "write"},
    )

    result = asyncio.run(
        engine.advance(
            run=run,
            tasks=[blocked_task],
            approve=True,
            user_input=None,
            resume_token=None,
            resolution_payload=None,
        )
    )
    state = cast(dict[str, Any], result.state)
    execution_records = cast(list[dict[str, Any]], state["execution_records"])
    runtime_transcript = cast(dict[str, Any], state["runtime_transcript"])

    assert result.status is WorkflowRunStatus.BLOCKED
    assert blocked_task.status is TaskNodeStatus.BLOCKED
    assert blocked_task.metadata_json["last_attempt_status"] == TaskNodeStatus.BLOCKED.value
    assert blocked_task.metadata_json["execution_state"] == "blocked"
    assert result.approval_required is False
    assert execution_records[-1]["status"] == TaskNodeStatus.BLOCKED.value
    assert execution_records[-1]["output_json"]["execution_blocked"] is True
    assert execution_records[-1]["output_json"]["interrupt_behavior"] == "user_interaction"
    assert runtime_transcript["last_directive"] == "await_user_input"
    blocked_delta = next(
        delta
        for delta in cast(list[dict[str, Any]], runtime_transcript["deltas"])
        if delta["delta_id"] == execution_records[-1]["transcript_delta_id"]
    )
    assert cast(list[dict[str, Any]], blocked_delta["tool_error_blocks"])


def test_workflow_directive_stops_remaining_tasks_in_same_cycle() -> None:
    import asyncio

    executed_names: list[str] = []

    class StubExecutor:
        def execute(self, *, context: object, task: Any) -> ExecutionResult:
            del context
            from datetime import UTC, datetime

            executed_names.append(cast(str, task.name))
            now = datetime.now(UTC)
            if task.name == "needs.input":
                return ExecutionResult(
                    trace_id=f"trace-{task.name}",
                    source_type="runtime",
                    source_name="test-executor",
                    command_or_action=f"execute:{task.name}",
                    input_payload={"task": task.name},
                    output_payload={
                        "stdout": "",
                        "stderr": "tool execution blocked pending user interaction",
                        "exit_code": 1,
                        "execution_blocked": True,
                        "interaction_required": True,
                        "interrupt_behavior": "user_interaction",
                        "block_reason": "user_interaction_required",
                    },
                    status=TaskNodeStatus.BLOCKED,
                    started_at=now,
                    ended_at=now,
                )
            raise AssertionError("loop should stop after await_user_input directive")

        def resolve_tool_spec(self, task: TaskNode) -> ToolSpec:
            del task
            return ToolSpec(
                name="workflow.structured_runtime",
                category=ToolCategory.EXECUTION,
                capability=ToolCapability.STRUCTURED_RUNTIME,
                safety_profile=ToolSafetyProfile(
                    writes_state=True,
                    is_concurrency_safe=False,
                    is_read_only=False,
                    is_destructive=True,
                ),
                access_mode=ToolAccessMode.WRITE,
            )

    class StubSessionRepository:
        def get_session(self, session_id: str) -> Session | None:
            del session_id
            return None

    class StubCapabilityFacade:
        def build_prompt_fragments(self, **_: object) -> dict[str, str]:
            return {
                "inventory_summary": "",
                "schema_summary": "",
                "prompt_fragment": "",
            }

    engine = WorkflowLoopEngine(
        executor=cast(Any, StubExecutor()),
        reflector=Reflector(),
        max_active_execution_records=10,
        max_active_messages=10,
        session_repository=cast(Any, StubSessionRepository()),
        run_log_repository=cast(Any, _StubRunLogRepository()),
        graph_repository=cast(Any, object()),
        capability_facade=cast(Any, StubCapabilityFacade()),
    )

    run = WorkflowRun(
        session_id="session-1",
        template_name="authorized-assessment",
        status=WorkflowRunStatus.RUNNING,
        current_stage="validation",
        state_json={
            "goal": "directive stop test",
            "runtime_policy": {},
            "batch": {"max_nodes_per_cycle": 2},
        },
    )
    blocked_task = TaskNode(
        workflow_run_id=run.id,
        name="needs.input",
        node_type=TaskNodeType.TASK,
        status=TaskNodeStatus.READY,
        sequence=1,
        metadata_json={"stage_key": "validation", "scheduler_access_mode": "write"},
    )
    skipped_task = TaskNode(
        workflow_run_id=run.id,
        name="should.not.run",
        node_type=TaskNodeType.TASK,
        status=TaskNodeStatus.READY,
        sequence=2,
        metadata_json={"stage_key": "validation", "scheduler_access_mode": "write"},
    )

    result = asyncio.run(
        engine.advance(
            run=run,
            tasks=[blocked_task, skipped_task],
            approve=True,
            user_input=None,
            resume_token=None,
            resolution_payload=None,
        )
    )
    state = cast(dict[str, Any], result.state)
    execution_records = cast(list[dict[str, Any]], state["execution_records"])
    runtime_transcript = cast(dict[str, Any], state["runtime_transcript"])

    assert result.status is WorkflowRunStatus.BLOCKED
    assert executed_names == ["needs.input"]
    assert result.executed_task_ids == [blocked_task.id]
    assert blocked_task.status is TaskNodeStatus.BLOCKED
    assert skipped_task.status is TaskNodeStatus.READY
    assert len(execution_records) == 1
    assert execution_records[0]["task_node_id"] == blocked_task.id
    assert execution_records[0]["output_json"]["execution_blocked"] is True
    assert runtime_transcript["last_directive"] == "await_user_input"
    assert len(cast(list[dict[str, Any]], runtime_transcript["tool_result_records"])) == 1


def test_parallel_policy_denial_does_not_contaminate_sibling_read_task(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    def policy_aware_execute(self: object, *, context: object, task: Any) -> ExecutionResult:
        del self, context
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        if task.name == "context_collect.attack_surface":
            return ExecutionResult(
                trace_id=f"trace-denied-{task.name}",
                source_type="runtime",
                source_name="test-executor",
                command_or_action=f"execute:{task.name}",
                input_payload={"task": task.name},
                output_payload={
                    "status": "failed",
                    "policy_denied": True,
                    "policy_reason": "denied for test",
                    "stderr": "denied for test",
                    "artifacts": [],
                    "citations": [],
                },
                status=TaskNodeStatus.FAILED,
                started_at=now,
                ended_at=now,
            )
        return ExecutionResult(
            trace_id=f"trace-ok-{task.name}",
            source_type="runtime",
            source_name="test-executor",
            command_or_action=f"execute:{task.name}",
            input_payload={"task": task.name},
            output_payload={"status": "completed", "artifacts": [], "citations": []},
            status=TaskNodeStatus.COMPLETED,
            started_at=now,
            ended_at=now,
        )

    monkeypatch.setattr("app.agent.executor.Executor.execute", policy_aware_execute)

    session_id = _create_session(client, goal="Verify isolated policy denial in parallel reads")
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    target_cycle: int | None = None
    payload: dict[str, Any] | None = None
    task_index: dict[str, str] = {}
    target_names = {"context_collect.attack_surface", "context_collect.existing_evidence"}

    for _ in range(12):
        advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
        assert advance.status_code == 200
        payload = cast(dict[str, Any], api_data(advance))
        state = cast(dict[str, Any], payload["state"])
        task_index = {
            cast(str, task["id"]): cast(str, task["name"])
            for task in cast(list[dict[str, Any]], payload["tasks"])
        }
        for record in cast(list[dict[str, Any]], state["execution_records"]):
            cycle = record.get("batch_cycle")
            task_id = record.get("task_node_id")
            if not isinstance(cycle, int) or not isinstance(task_id, str):
                continue
            if task_index.get(task_id) not in target_names:
                continue
            cycle_names = {
                task_index.get(cast(str, item.get("task_node_id")), "")
                for item in cast(list[dict[str, Any]], state["execution_records"])
                if item.get("batch_cycle") == cycle
            }
            if target_names.issubset(cycle_names):
                target_cycle = cycle
                break
        if target_cycle is not None:
            break
    else:
        assert False, "parallel read cycle with isolated policy denial was not observed"

    assert payload is not None
    state = cast(dict[str, Any], payload["state"])
    records = [
        record
        for record in cast(list[dict[str, Any]], state["execution_records"])
        if record.get("batch_cycle") == target_cycle
    ]
    records_by_name = {
        task_index[cast(str, record["task_node_id"])]: record
        for record in records
        if isinstance(record.get("task_node_id"), str)
        and cast(str, record["task_node_id"]) in task_index
        and task_index[cast(str, record["task_node_id"])] in target_names
    }
    denied_record = records_by_name["context_collect.attack_surface"]
    sibling_record = records_by_name["context_collect.existing_evidence"]
    assert denied_record["status"] == "failed"
    assert cast(dict[str, Any], denied_record["output_json"])["policy_denied"] is True
    assert sibling_record["status"] == "completed"

    latest_cycle = cast(list[dict[str, Any]], cast(dict[str, Any], state["loop"])["cycles"])[-1]
    partial_failures = cast(list[dict[str, Any]], latest_cycle["partial_failures"])
    assert any(item["task_name"] == "context_collect.attack_surface" for item in partial_failures)
    assert all(
        item["task_name"] != "context_collect.existing_evidence" for item in partial_failures
    )


def test_start_workflow_carries_session_runtime_policy_into_workflow_state(
    client: TestClient,
) -> None:
    session_response = client.post(
        "/api/sessions",
        json={
            "goal": "Policy aware workflow",
            "runtime_policy_json": {
                "allow_network": False,
                "allow_write": True,
                "max_execution_seconds": 120,
                "max_command_length": 2048,
            },
        },
    )
    session_id = cast(str, api_data(session_response)["id"])

    workflow = _start_workflow(client, session_id)
    state = cast(dict[str, Any], workflow["state"])
    assert state["runtime_policy"] == {
        "allow_network": False,
        "allow_write": True,
        "max_execution_seconds": 120,
        "max_command_length": 2048,
    }


def test_run_scoped_export_and_replay_endpoints_return_structured_payloads(
    client: TestClient,
) -> None:
    session_id = _create_session(client, goal="Export and replay run-scoped workflow")
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
    assert advance.status_code == 200

    export_response = client.get(f"/api/workflows/{run_id}/export")
    assert export_response.status_code == 200
    export_payload = cast(dict[str, Any], api_data(export_response))
    assert cast(dict[str, Any], export_payload["run"])["id"] == run_id
    assert cast(dict[str, Any], export_payload["task_graph"])["workflow_run_id"] == run_id
    assert cast(dict[str, Any], export_payload["evidence_graph"])["workflow_run_id"] == run_id
    assert cast(dict[str, Any], export_payload["causal_graph"])["workflow_run_id"] == run_id
    assert cast(dict[str, Any], export_payload["attack_graph"])["workflow_run_id"] == run_id
    assert isinstance(export_payload["execution_records"], list)
    assert "batch_state" in export_payload

    replay_response = client.get(f"/api/workflows/{run_id}/replay")
    assert replay_response.status_code == 200
    replay_payload = cast(dict[str, Any], api_data(replay_response))
    assert replay_payload["run_id"] == run_id
    replay_steps = cast(list[dict[str, Any]], replay_payload["replay_steps"])
    assert replay_steps
    assert "batch_cycle" in replay_steps[0]
    assert "retry_attempt" in replay_steps[0]


def test_failed_execution_adds_retry_metadata_and_append_only_replan_records(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    call_state = {"count": 0}

    def flaky_execute(self: object, *, context: object, task: Any) -> ExecutionResult:
        del self, context
        from datetime import UTC, datetime

        call_state["count"] += 1
        now = datetime.now(UTC)
        status = (
            TaskNodeStatus.FAILED
            if call_state["count"] == 1 and task.name == "scope_guard"
            else TaskNodeStatus.COMPLETED
        )
        return ExecutionResult(
            trace_id=f"trace-test-{call_state['count']}",
            source_type="runtime",
            source_name="test-executor",
            command_or_action=f"execute:{task.name}",
            input_payload={"task": task.name},
            output_payload={"status": status.value},
            status=status,
            started_at=now,
            ended_at=now,
        )

    monkeypatch.setattr("app.agent.executor.Executor.execute", flaky_execute)

    session_id = _create_session(client, goal="Trigger retry and replan metadata")
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    first_advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
    assert first_advance.status_code == 200
    first_payload = cast(dict[str, Any], api_data(first_advance))
    first_state = cast(dict[str, Any], first_payload["state"])
    replan_records = cast(list[dict[str, Any]], first_state["replan_records"])
    assert len(replan_records) == 1
    assert replan_records[0]["suggestion"] == "retry_or_replan"
    first_tasks = _workflow_tasks_by_name(first_payload)
    assert first_tasks["scope_guard"]["metadata"]["retry_count"] == 1
    assert first_tasks["scope_guard"]["metadata"]["retry_scheduled"] is True

    second_advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
    assert second_advance.status_code == 200
    second_payload = cast(dict[str, Any], api_data(second_advance))
    second_state = cast(dict[str, Any], second_payload["state"])
    second_replan_records = cast(list[dict[str, Any]], second_state["replan_records"])
    assert len(second_replan_records) >= 1


def test_workflow_template_discovery_and_start_time_selection(client: TestClient) -> None:
    templates_response = client.get("/api/workflows/templates")
    assert templates_response.status_code == 200
    templates = cast(list[dict[str, Any]], api_data(templates_response))
    template_names = {cast(str, item["name"]) for item in templates}
    assert "authorized-assessment" in template_names
    assert "authorized-assessment-extended" in template_names

    session_id = _create_session(client, goal="Start workflow with explicit template selection")
    selected_start = client.post(
        "/api/workflows/start",
        json={
            "session_id": session_id,
            "template_name": "authorized-assessment-extended",
        },
    )
    assert selected_start.status_code == 201
    selected_payload = cast(dict[str, Any], api_data(selected_start))
    assert selected_payload["template_name"] == "authorized-assessment-extended"


def test_role_prompt_metadata_propagates_to_tasks_and_executor_input(client: TestClient) -> None:
    session_id = _create_session(client, goal="Validate role prompt propagation")
    workflow = _start_workflow(client, session_id, template_name="authorized-assessment-extended")
    run_id = cast(str, workflow["id"])
    tasks = _workflow_tasks_by_name(workflow)

    task_payload = tasks["context_collect.attack_surface"]
    task_id = cast(str, task_payload["id"])
    metadata = cast(dict[str, Any], task_payload["metadata"])
    assert isinstance(metadata.get("role_prompt"), str) and metadata["role_prompt"]
    assert (
        isinstance(metadata.get("sub_agent_role_prompt"), str) and metadata["sub_agent_role_prompt"]
    )

    found_record: dict[str, Any] | None = None
    for _ in range(25):
        advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
        assert advance.status_code == 200
        payload = cast(dict[str, Any], api_data(advance))
        state = cast(dict[str, Any], payload["state"])
        records = cast(list[dict[str, Any]], state["execution_records"])
        found_record = next(
            (record for record in records if record.get("task_node_id") == task_id),
            None,
        )
        if found_record is not None:
            break

    assert found_record is not None
    input_json = cast(dict[str, Any], found_record["input_json"])
    assert input_json["role_prompt"] == metadata["role_prompt"]
    assert input_json["sub_agent_role_prompt"] == metadata["sub_agent_role_prompt"]


def test_reorder_sibling_task_priorities_changes_runtime_execution_order(
    client: TestClient,
) -> None:
    session_id = _create_session(client, goal="Reorder sibling tasks before execution")
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])
    tasks = _workflow_tasks_by_name(workflow)

    first_task_id = cast(str, tasks["context_collect.attack_surface"]["id"])
    second_task_id = cast(str, tasks["context_collect.existing_evidence"]["id"])

    reorder_response = client.post(
        f"/api/workflows/{run_id}/tasks/reorder-priority",
        json={"ordered_task_ids": [second_task_id, first_task_id]},
    )
    assert reorder_response.status_code == 200
    reorder_payload = cast(dict[str, Any], api_data(reorder_response))
    reordered_tasks = {
        task["id"]: cast(dict[str, Any], task["metadata"])
        for task in cast(list[dict[str, Any]], reorder_payload["tasks"])
    }
    assert reordered_tasks[second_task_id]["sibling_priority_rank"] == 0
    assert reordered_tasks[first_task_id]["sibling_priority_rank"] == 1

    execution_order: list[str] = []
    for _ in range(30):
        advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
        assert advance.status_code == 200
        payload = cast(dict[str, Any], api_data(advance))
        state = cast(dict[str, Any], payload["state"])
        batch_state = cast(dict[str, Any], state["batch"])
        batch_executed = [
            task_id for task_id in cast(list[str], batch_state["executed_task_ids"]) if task_id
        ]
        if second_task_id in batch_executed and first_task_id in batch_executed:
            execution_order = batch_executed
            break

    assert execution_order
    assert execution_order.index(second_task_id) < execution_order.index(first_task_id)


def test_execution_context_compaction_archives_old_records_and_replay_export_remain_compatible(
    client: TestClient,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(Coordinator, "MAX_ACTIVE_EXECUTION_RECORDS", 2)

    session_id = _create_session(client, goal="Trigger workflow execution compaction")
    workflow = _start_workflow(client, session_id)
    run_id = cast(str, workflow["id"])

    latest_payload: dict[str, Any] | None = None
    for _ in range(8):
        advance = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
        assert advance.status_code == 200
        latest_payload = cast(dict[str, Any], api_data(advance))

    assert latest_payload is not None
    state = cast(dict[str, Any], latest_payload["state"])
    archived_records = cast(list[dict[str, Any]], state["archived_execution_records"])
    active_records = cast(list[dict[str, Any]], state["execution_records"])
    compaction = cast(dict[str, Any], state["compaction"])
    execution_compaction = cast(dict[str, Any], compaction["execution"])

    assert archived_records
    assert len(active_records) <= 2
    assert execution_compaction["trim_count"] >= 1
    assert execution_compaction["archived_count"] == len(archived_records)

    export_response = client.get(f"/api/workflows/{run_id}/export")
    assert export_response.status_code == 200
    export_payload = cast(dict[str, Any], api_data(export_response))
    exported_records = cast(list[dict[str, Any]], export_payload["execution_records"])
    assert len(exported_records) == len(archived_records) + len(active_records)

    replay_response = client.get(f"/api/workflows/{run_id}/replay")
    assert replay_response.status_code == 200
    replay_payload = cast(dict[str, Any], api_data(replay_response))
    replay_steps = cast(list[dict[str, Any]], replay_payload["replay_steps"])
    assert len(replay_steps) == len(exported_records)


def test_runnable_selector_groups_read_and_write_tasks_without_changing_flat_order() -> None:
    selector = WorkflowRunnableSelector(
        tool_spec_resolver=lambda task: (
            ToolSpec(
                name=f"tool.{task.name}",
                category=ToolCategory.DISCOVERY,
                capability=ToolCapability.CAPABILITY_SNAPSHOT,
                safety_profile=ToolSafetyProfile(
                    writes_state=task.name.endswith("write"),
                    is_concurrency_safe=(False if task.name.endswith("write") else None),
                ),
                access_mode=(
                    ToolAccessMode.WRITE if task.name.endswith("write") else ToolAccessMode.READ
                ),
            )
        ),
    )
    tasks = [
        TaskNode(
            workflow_run_id="run-1",
            name="alpha_read",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.READY,
            sequence=1,
            metadata_json={"stage_key": "discovery", "priority": 30, "approval_required": False},
        ),
        TaskNode(
            workflow_run_id="run-1",
            name="beta_write",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.READY,
            sequence=2,
            metadata_json={"stage_key": "validation", "priority": 20, "approval_required": False},
        ),
        TaskNode(
            workflow_run_id="run-1",
            name="gamma_read",
            node_type=TaskNodeType.TASK,
            status=TaskNodeStatus.READY,
            sequence=3,
            metadata_json={"stage_key": "analysis", "priority": 10, "approval_required": False},
        ),
    ]

    selection = selector.select(tasks=tasks, state={"batch": {"max_nodes_per_cycle": 3}})

    assert [task.task_name for task in selection.selected_tasks] == [
        "alpha_read",
        "beta_write",
        "gamma_read",
    ]
    assert [task.task_name for task in selection.parallel_read_group] == [
        "alpha_read",
        "gamma_read",
    ]
    assert [task.task_name for task in selection.serialized_write_group] == ["beta_write"]
    assert [task.scheduler_group for task in selection.selected_tasks] == [
        "parallel_read_group",
        "serialized_write_group",
        "parallel_read_group",
    ]
    assert [task.access_mode for task in selection.selected_tasks] == ["read", "write", "read"]
    assert [task.is_read_only for task in selection.selected_tasks] == [True, False, True]
    assert [task.is_concurrency_safe for task in selection.selected_tasks] == [True, False, True]
    assert [task.is_destructive for task in selection.selected_tasks] == [False, True, False]


def test_runnable_selector_does_not_allow_metadata_to_weaken_concrete_tool_safety() -> None:
    selector = WorkflowRunnableSelector(
        tool_spec_resolver=lambda task: ToolSpec(
            name="workflow.stage_transition",
            category=ToolCategory.ORCHESTRATION,
            capability=ToolCapability.STAGE_TRANSITION,
            safety_profile=ToolSafetyProfile(
                writes_state=True,
                is_concurrency_safe=False,
                is_read_only=False,
                is_destructive=False,
            ),
            access_mode=ToolAccessMode.WRITE,
        ),
    )
    task = TaskNode(
        workflow_run_id="run-1",
        name="stage_guard",
        node_type=TaskNodeType.TASK,
        status=TaskNodeStatus.READY,
        sequence=1,
        metadata_json={
            "stage_key": "scope",
            "scheduler_access_mode": "read",
            "scheduler_is_read_only": True,
            "scheduler_is_concurrency_safe": True,
            "scheduler_is_destructive": False,
        },
    )

    selection = selector.select(tasks=[task], state={"batch": {"max_nodes_per_cycle": 1}})

    assert [item.task_name for item in selection.parallel_read_group] == []
    assert [item.task_name for item in selection.serialized_write_group] == ["stage_guard"]
    selected = selection.selected_tasks[0]
    assert selected.access_mode == "write"
    assert selected.is_read_only is False
    assert selected.is_concurrency_safe is False
    assert selected.scheduler_group == "serialized_write_group"


def test_tool_scheduler_preserves_write_order_and_read_phase_boundaries() -> None:
    schedule = WorkflowToolScheduler().build_schedule(
        RunnableSelection(
            batch_size=4,
            selected_tasks=[
                SelectedTask(
                    task_id="alpha",
                    task_name="alpha_read",
                    stage_key="discovery",
                    priority=40,
                    approval_required=False,
                    tool_name="tool.alpha",
                    writes_state=False,
                    is_concurrency_safe=True,
                    is_read_only=True,
                    is_destructive=False,
                    scheduler_group="parallel_read_group",
                    access_mode="read",
                ),
                SelectedTask(
                    task_id="beta",
                    task_name="beta_write",
                    stage_key="analysis",
                    priority=30,
                    approval_required=False,
                    tool_name="tool.beta",
                    writes_state=True,
                    is_concurrency_safe=False,
                    is_read_only=False,
                    is_destructive=True,
                    scheduler_group="serialized_write_group",
                    access_mode="write",
                ),
                SelectedTask(
                    task_id="gamma",
                    task_name="gamma_write",
                    stage_key="analysis",
                    priority=20,
                    approval_required=False,
                    tool_name="tool.gamma",
                    writes_state=True,
                    is_concurrency_safe=False,
                    is_read_only=False,
                    is_destructive=True,
                    scheduler_group="serialized_write_group",
                    access_mode="write",
                ),
                SelectedTask(
                    task_id="delta",
                    task_name="delta_read",
                    stage_key="reporting",
                    priority=10,
                    approval_required=False,
                    tool_name="tool.delta",
                    writes_state=False,
                    is_concurrency_safe=True,
                    is_read_only=True,
                    is_destructive=False,
                    scheduler_group="parallel_read_group",
                    access_mode="read",
                ),
            ],
            parallel_read_group=[],
            serialized_write_group=[],
        )
    )

    assert [phase.scheduler_group for phase in schedule.phases] == [
        "parallel_read_group",
        "serialized_write_group",
        "serialized_write_group",
        "parallel_read_group",
    ]
    assert [phase.task_ids for phase in schedule.phases] == [
        ["alpha"],
        ["beta"],
        ["gamma"],
        ["delta"],
    ]


def test_tool_registry_policy_denial_returns_failed_result_without_approval_state() -> None:
    class DenyAllPolicy:
        def evaluate(self, *, request: ToolExecutionRequest, spec: ToolSpec) -> ToolPolicyDecision:
            del request, spec
            return ToolPolicyDecision.deny("blocked by policy", metadata={"allow_write": False})

    registry = ToolRegistry(policy=DenyAllPolicy(), hooks=NoOpToolExecutionHooks())
    runtime_spec = ToolSpec(
        name="workflow.structured_runtime",
        category=ToolCategory.EXECUTION,
        capability=ToolCapability.STRUCTURED_RUNTIME,
        safety_profile=ToolSafetyProfile(writes_state=True, uses_runtime=True),
    )
    registry.register(
        spec=runtime_spec,
        matcher=lambda _task: True,
        handler=lambda request: (_ for _ in ()).throw(AssertionError(request.task.name)),
    )
    task = TaskNode(
        workflow_run_id="run-1",
        name="safe_validation.validate_primary_hypothesis",
        node_type=TaskNodeType.TASK,
        status=TaskNodeStatus.READY,
        sequence=1,
        metadata_json={"stage_key": "validation", "approval_required": True},
    )
    context = WorkflowExecutionContext(
        session_id="session-1",
        workflow_run_id="run-1",
        goal="test policy denial",
        template_name="authorized-assessment",
        current_stage="validation",
        runtime_policy={"allow_write": False},
    )

    result = registry.execute(context=context, task=task)

    assert result.status is TaskNodeStatus.FAILED
    assert result.output_payload["policy_denied"] is True
    assert result.output_payload["policy_reason"] == "blocked by policy"
    assert result.output_payload.get("approval_required") is None
    assert result.command_or_action == f"execute:{task.name}"


def test_tool_registry_invokes_before_after_and_error_hooks() -> None:
    class RecordingHooks:
        def __init__(self) -> None:
            self.events: list[str] = []

        def before_execution(self, *, request: ToolExecutionRequest, spec: ToolSpec) -> None:
            self.events.append(f"before:{spec.name}:{request.task.name}")

        def after_execution(self, *, request: ToolExecutionRequest, result: Any) -> None:
            self.events.append(f"after:{result.spec.name}:{request.task.name}")

        def on_execution_error(
            self, *, request: ToolExecutionRequest, spec: ToolSpec, error: Exception
        ) -> None:
            self.events.append(f"error:{spec.name}:{request.task.name}:{error}")

    hooks = RecordingHooks()
    registry = ToolRegistry(hooks=hooks)
    success_spec = ToolSpec(
        name="workflow.success",
        category=ToolCategory.DISCOVERY,
        capability=ToolCapability.CAPABILITY_SNAPSHOT,
    )
    failure_spec = ToolSpec(
        name="workflow.failure",
        category=ToolCategory.EXECUTION,
        capability=ToolCapability.STRUCTURED_RUNTIME,
    )

    def success_handler(request: ToolExecutionRequest) -> ToolExecutionResult:
        return ToolExecutionResult(
            spec=success_spec,
            source_type="runtime",
            source_name="test",
            command_or_action=f"execute:{request.task.name}",
            input_payload={"trace_id": request.trace_id},
            output_payload={"status": "ok"},
            status=TaskNodeStatus.COMPLETED,
            started_at=request.started_at,
            ended_at=request.started_at,
        )

    def failure_handler(request: ToolExecutionRequest) -> ToolExecutionResult:
        raise RuntimeError(f"boom:{request.task.name}")

    registry.register(
        spec=success_spec, matcher=lambda task: task.name == "success", handler=success_handler
    )
    registry.register(spec=failure_spec, matcher=lambda _task: True, handler=failure_handler)
    context = WorkflowExecutionContext(
        session_id="session-1",
        workflow_run_id="run-1",
        goal="test hooks",
        template_name="authorized-assessment",
        current_stage="analysis",
        runtime_policy={},
    )
    success_task = TaskNode(
        workflow_run_id="run-1",
        name="success",
        node_type=TaskNodeType.TASK,
        status=TaskNodeStatus.READY,
        sequence=1,
        metadata_json={},
    )
    failure_task = TaskNode(
        workflow_run_id="run-1",
        name="failure",
        node_type=TaskNodeType.TASK,
        status=TaskNodeStatus.READY,
        sequence=2,
        metadata_json={},
    )

    registry.execute(context=context, task=success_task)
    try:
        registry.execute(context=context, task=failure_task)
    except RuntimeError as error:
        assert str(error) == "boom:failure"
    else:
        assert False, "expected runtime error"

    assert hooks.events == [
        "before:workflow.success:success",
        "after:workflow.success:success",
        "before:workflow.failure:failure",
        "error:workflow.failure:failure:boom:failure",
    ]
