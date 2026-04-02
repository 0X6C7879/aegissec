from __future__ import annotations

from typing import Any, cast

from fastapi.testclient import TestClient

from app.agent.coordinator import Coordinator
from app.agent.executor import ExecutionResult
from app.agent.selection import WorkflowRunnableSelector
from app.agent.tool_registry import (
    NoOpToolExecutionHooks,
    ToolCapability,
    ToolCategory,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolPolicyDecision,
    ToolRegistry,
    ToolSafetyProfile,
    ToolSpec,
)
from app.agent.workflow import WorkflowExecutionContext
from app.db.models import TaskNode, TaskNodeStatus, TaskNodeType
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

    tasks = _workflow_tasks_by_name(blocked_payload)
    assert tasks["safe_validation.validate_primary_hypothesis"]["status"] == "blocked"
    assert tasks["safe_validation.validate_primary_hypothesis"]["metadata"]["execution_state"] == (
        "waiting_approval"
    )

    approved = client.post(f"/api/workflows/{run_id}/advance", json={"approve": True})
    assert approved.status_code == 200
    approved_payload = cast(dict[str, Any], api_data(approved))
    assert approved_payload["status"] == "running"
    approved_tasks = _workflow_tasks_by_name(approved_payload)
    assert approved_tasks["safe_validation.validate_primary_hypothesis"]["status"] == "completed"
    assert (
        approved_tasks["safe_validation.validate_primary_hypothesis"]["metadata"]["execution_state"]
        == "success"
    )


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

    task_graph = client.get(f"/api/sessions/{session_id}/graphs/task")
    evidence_graph = client.get(f"/api/sessions/{session_id}/graphs/evidence")
    causal_graph = client.get(f"/api/sessions/{session_id}/graphs/causal")

    assert task_graph.status_code == 200
    assert evidence_graph.status_code == 200
    assert causal_graph.status_code == 200

    task_payload = cast(dict[str, Any], api_data(task_graph))
    evidence_payload = cast(dict[str, Any], api_data(evidence_graph))
    causal_payload = cast(dict[str, Any], api_data(causal_graph))

    assert task_payload["graph_type"] == "task"
    assert evidence_payload["graph_type"] == "evidence"
    assert causal_payload["graph_type"] == "causal"
    assert len(cast(list[Any], evidence_payload["nodes"])) > 0
    assert len(cast(list[Any], causal_payload["nodes"])) > 0


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
    assert isinstance(latest_cycle["parallel_read_group"], list)
    assert isinstance(latest_cycle["serialized_write_group"], list)
    assert isinstance(latest_cycle["scheduler_summary"], dict)
    assert isinstance(latest_cycle["retrieval_summary"], str)
    assert isinstance(latest_cycle["tool_results"], list)
    assert isinstance(latest_cycle["reflection_summary"], str)
    assert isinstance(latest_cycle["memory_writes"], list)
    assert isinstance(latest_cycle["compaction_summary"], dict)
    assert latest_cycle["next_action"] in {"continue", "complete", "await_approval", "idle"}
    scheduler_summary = cast(dict[str, Any], latest_cycle["scheduler_summary"])
    assert scheduler_summary["mode"] == "phase2_sequential"
    assert scheduler_summary["selected_task_ids"] == [
        task["task_id"] for task in cast(list[dict[str, Any]], latest_cycle["selected_tasks"])
    ]


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
    retrieval = cast(dict[str, Any], context["retrieval"])
    memory = cast(dict[str, Any], context["memory"])
    projection = cast(dict[str, Any], context["projection"])
    prompting = cast(dict[str, Any], context["prompting"])

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

    loop_state = cast(dict[str, Any], state["loop"])
    latest_cycle = cast(list[dict[str, Any]], loop_state["cycles"])[-1]
    assert isinstance(latest_cycle["retrieval"], dict)
    assert isinstance(latest_cycle["memory"], dict)
    assert isinstance(latest_cycle["context_projection"], dict)
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
        events = [websocket.receive_json() for _ in range(43)]

    event_types = [cast(str, event["type"]) for event in events]
    assert event_types[0] == "workflow.run.started"
    assert event_types[1] == "workflow.stage.changed"
    assert event_types.count("workflow.task.updated") == 19
    assert event_types.count("task.planned") == 19
    assert event_types.count("graph.updated") == 3


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
            + 3
        )
        events = [websocket.receive_json() for _ in range(event_count)]

    event_types = [cast(str, event["type"]) for event in events]
    assert event_types.count("workflow.stage.changed") == 1
    assert event_types.count("workflow.task.updated") == 19
    assert event_types.count("task.started") == 2
    assert event_types.count("task.finished") == 2
    assert event_types.count("graph.updated") == 3


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
                safety_profile=ToolSafetyProfile(writes_state=task.name.endswith("write")),
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
