from __future__ import annotations

from app.agent.assistant_runtime import AssistantExecutionContext, AssistantExecutionRuntime
from app.agent.assistant_turn import AssistantTurnInput
from app.agent.selection import RunnableSelection, SelectedTask
from app.agent.tool_scheduler import WorkflowToolSchedule, WorkflowToolScheduler
from app.agent.tool_wave import (
    AssistantExecutionFrame,
    ToolWaveCandidate,
    ToolWaveDecision,
    ToolWaveExecutionFrame,
)
from app.agent.turn_models import NextTurnDirective
from app.agent.turn_planner import AssistantTurnPlanner


def _selected_task(
    *,
    task_id: str,
    task_name: str,
    scheduler_group: str,
    writes_state: bool,
    is_read_only: bool,
) -> SelectedTask:
    return SelectedTask(
        task_id=task_id,
        task_name=task_name,
        stage_key="context_collect",
        priority=1,
        approval_required=False,
        tool_name=f"workflow.{task_name}",
        writes_state=writes_state,
        is_concurrency_safe=scheduler_group == "parallel_read_group",
        is_read_only=is_read_only,
        is_destructive=writes_state,
        scheduler_group=scheduler_group,
    )


def _schedule() -> WorkflowToolSchedule:
    read_task = _selected_task(
        task_id="task-read",
        task_name="read_task",
        scheduler_group="parallel_read_group",
        writes_state=False,
        is_read_only=True,
    )
    write_task = _selected_task(
        task_id="task-write",
        task_name="write_task",
        scheduler_group="serialized_write_group",
        writes_state=True,
        is_read_only=False,
    )
    selection = RunnableSelection(
        batch_size=2,
        selected_tasks=[read_task, write_task],
        parallel_read_group=[read_task],
        serialized_write_group=[write_task],
    )
    return WorkflowToolScheduler().build_schedule(selection)


def _authoritative_runtime() -> AssistantExecutionRuntime:
    workflow_candidate = ToolWaveCandidate(
        wave_id="workflow_selected_wave",
        wave_type="execution",
        scheduler_mode="phase3_read_parallel_write_serial",
        task_ids=["task-read", "task-write"],
        task_names=["read_task", "write_task"],
        rationale="assistant saw both runnable tasks",
        metadata={
            "parallel_read_task_ids": ["task-read"],
            "serialized_write_task_ids": ["task-write"],
            "candidate_source": "assistant_runtime",
        },
    )
    investigation_candidate = ToolWaveCandidate(
        wave_id="assistant_investigation_wave",
        wave_type="investigation",
        scheduler_mode="phase3_read_parallel_write_serial",
        task_ids=["task-read"],
        task_names=["read_task"],
        rationale="assistant chose the read-only investigation wave",
        metadata={
            "scheduler_group": "parallel_read_group",
            "candidate_source": "assistant_runtime",
        },
    )
    return AssistantExecutionRuntime(
        execution_context=AssistantExecutionContext(
            frame_id="assistant-frame-cycle-1",
            cycle_id="cycle-1",
            stage="context_collect",
            goal="authorized assessment",
            active_task_ids=["task-read", "task-write"],
            active_task_names=["read_task", "write_task"],
        ),
        execution_frame=AssistantExecutionFrame(
            frame_id="assistant-frame-cycle-1",
            cycle_id="cycle-1",
            candidate_waves=[workflow_candidate, investigation_candidate],
            chosen_wave=investigation_candidate,
            wave_decision=ToolWaveDecision(
                decision="investigate_with_recall_wave",
                selected_wave_id="assistant_investigation_wave",
                reason="assistant prioritized read-only evidence gathering",
                confidence=0.91,
            ),
        ),
        chosen_execution_wave=ToolWaveExecutionFrame(
            wave_id="assistant_investigation_wave",
            task_ids=["task-read"],
            task_names=["read_task"],
            scheduler_group="parallel_read_group",
            mode="execute",
        ),
    )


def test_turn_planner_prefers_authoritative_runtime_wave_projection() -> None:
    planner = AssistantTurnPlanner()
    schedule = _schedule()
    runtime = _authoritative_runtime()
    turn_input = AssistantTurnInput(
        turn_id="turn-1",
        cycle_id="cycle-1",
        current_goal="authorized assessment",
        stage="context_collect",
    )

    plan = planner.build_turn_plan(
        turn_input=turn_input,
        schedule=schedule,
        assistant_execution_runtime=runtime,
        candidate_waves=[{"wave_id": "stale_candidate"}],
        chosen_wave={"wave_id": "stale_wave"},
        wave_decision={"decision": "stale_decision", "selected_wave_id": "stale_wave"},
    )

    assert plan.candidate_waves == runtime.execution_frame.candidate_waves_state()
    assert plan.chosen_wave == runtime.execution_frame.chosen_wave_state()
    assert plan.wave_decision == runtime.execution_frame.wave_decision_state()
    assert plan.recommended_tool_wave["expected_task_ids"] == ["task-read"]
    assert plan.recommended_tool_wave["expected_task_names"] == ["read_task"]
    assert plan.recommended_tool_wave["parallel_read_task_ids"] == ["task-read"]
    assert plan.recommended_tool_wave["serialized_write_task_ids"] == []
    assert (
        plan.recommended_tool_wave["rationale"]
        == "assistant chose the read-only investigation wave"
    )


def test_turn_planner_assimilation_uses_authoritative_runtime_chosen_wave() -> None:
    planner = AssistantTurnPlanner()
    runtime = _authoritative_runtime()

    assimilation_result = planner.build_assimilation_result(
        mutable_state={},
        tool_results=[{"task_id": "task-read", "task_name": "read_task"}],
        partial_failures=[],
        next_directive=NextTurnDirective.CONTINUE,
        chosen_wave={"wave_id": "stale_wave", "task_ids": ["task-write"]},
        assistant_execution_runtime=runtime,
    )

    assert assimilation_result["selected_wave_id"] == "assistant_investigation_wave"
    assert assimilation_result["expected_task_count"] == 1
    assert assimilation_result["executed_task_count"] == 1
    assert assimilation_result["executed_task_ids"] == ["task-read"]
    assert assimilation_result["status"] == "assimilated"


def test_assistant_execution_runtime_state_round_trip_preserves_wave_authority() -> None:
    runtime = _authoritative_runtime()

    restored_runtime = AssistantExecutionRuntime.from_state(runtime.to_state())

    assert restored_runtime is not None
    assert (
        restored_runtime.execution_frame.candidate_waves_state()
        == runtime.execution_frame.candidate_waves_state()
    )
    assert (
        restored_runtime.execution_frame.chosen_wave_state()
        == runtime.execution_frame.chosen_wave_state()
    )
    assert (
        restored_runtime.execution_frame.wave_decision_state()
        == runtime.execution_frame.wave_decision_state()
    )
    assert (
        restored_runtime.chosen_execution_wave.to_state()
        == runtime.chosen_execution_wave.to_state()
    )
