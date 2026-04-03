from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from app.agent.assistant_turn import (
    AssistantTurnInput,
    AssistantTurnOutcome,
    AssistantTurnPlan,
    AssistantTurnState,
)
from app.agent.context_models import ContextSnapshot
from app.agent.loop_models import WorkflowCycleArtifact
from app.agent.tool_scheduler import WorkflowToolSchedule
from app.agent.turn_models import NextTurnDirective


@dataclass(frozen=True)
class AssistantTurnBundle:
    turn_input: AssistantTurnInput
    turn_plan: AssistantTurnPlan
    turn_outcome: AssistantTurnOutcome


class AssistantTurnPlanner:
    def build_bundle(
        self,
        *,
        mutable_state: dict[str, object],
        cycle_id: str,
        context_snapshot: ContextSnapshot,
        schedule: WorkflowToolSchedule,
        tool_results: list[dict[str, object]],
        reflection_summary: str,
        partial_failures: list[dict[str, object]],
        next_action: str,
        next_directive: NextTurnDirective,
    ) -> AssistantTurnBundle:
        turn_id = f"assistant-turn-{uuid4()}"
        turn_input = self.build_turn_input(
            mutable_state=mutable_state,
            turn_id=turn_id,
            cycle_id=cycle_id,
            context_snapshot=context_snapshot,
            schedule=schedule,
        )
        turn_plan = self.build_turn_plan(
            turn_input=turn_input,
            schedule=schedule,
        )
        turn_outcome = self.build_turn_outcome(
            turn_input=turn_input,
            turn_plan=turn_plan,
            tool_results=tool_results,
            reflection_summary=reflection_summary,
            partial_failures=partial_failures,
            next_action=next_action,
            next_directive=next_directive,
        )
        return AssistantTurnBundle(
            turn_input=turn_input,
            turn_plan=turn_plan,
            turn_outcome=turn_outcome,
        )

    def build_turn_input(
        self,
        *,
        mutable_state: dict[str, object],
        turn_id: str,
        cycle_id: str,
        context_snapshot: ContextSnapshot,
        schedule: WorkflowToolSchedule,
    ) -> AssistantTurnInput:
        prior_outcome = self.latest_outcome(mutable_state)
        active_tasks = [task.task_name for task in schedule.selected_tasks]
        if not active_tasks:
            current_stage = self._string(mutable_state.get("current_stage"))
            if current_stage is not None:
                active_tasks = [current_stage]
        return AssistantTurnInput(
            turn_id=turn_id,
            cycle_id=cycle_id,
            current_goal=str(mutable_state.get("goal") or "authorized assessment"),
            stage=self._string(mutable_state.get("current_stage")),
            active_tasks=active_tasks,
            retrieval_context=self._retrieval_context(context_snapshot),
            memory_context=self._memory_context(context_snapshot),
            transcript_context=self._transcript_context(
                context_snapshot=context_snapshot,
                prior_outcome=prior_outcome,
            ),
            reasoning_frame=self._reasoning_frame(
                context_snapshot=context_snapshot,
                prior_outcome=prior_outcome,
                schedule=schedule,
            ),
        )

    def build_turn_plan(
        self,
        *,
        turn_input: AssistantTurnInput,
        schedule: WorkflowToolSchedule,
    ) -> AssistantTurnPlan:
        selected_task_ids = [task.task_id for task in schedule.selected_tasks]
        selected_task_names = [task.task_name for task in schedule.selected_tasks]
        recommended_tool_wave = self.resolve_turn_wave(schedule=schedule)
        rationale = str(recommended_tool_wave.get("rationale") or "")
        return AssistantTurnPlan(
            turn_id=turn_input.turn_id,
            cycle_id=turn_input.cycle_id,
            recommended_tool_wave=recommended_tool_wave,
            scheduler_mode=schedule.scheduler_mode,
            selected_task_ids=selected_task_ids,
            selected_task_names=selected_task_names,
            rationale=rationale,
        )

    def resolve_turn_wave(self, *, schedule: WorkflowToolSchedule) -> dict[str, object]:
        expected_task_ids = [task.task_id for task in schedule.selected_tasks]
        expected_task_names = [task.task_name for task in schedule.selected_tasks]
        parallel_read_task_ids = [task.task_id for task in schedule.parallel_read_group]
        serialized_write_task_ids = [task.task_id for task in schedule.serialized_write_group]
        if not expected_task_ids:
            rationale = (
                "No workflow-selected tasks are ready, so the recommended tool wave is empty."
            )
        else:
            rationale = (
                "Run the workflow-selected wave in "
                f"{schedule.scheduler_mode or 'unspecified'} mode; "
                f"parallel read tasks={len(parallel_read_task_ids)}, "
                f"serialized write tasks={len(serialized_write_task_ids)}."
            )
        return {
            "scheduler_mode": schedule.scheduler_mode,
            "expected_task_ids": expected_task_ids,
            "expected_task_names": expected_task_names,
            "parallel_read_task_ids": parallel_read_task_ids,
            "serialized_write_task_ids": serialized_write_task_ids,
            "expected_wave_size": len(expected_task_ids),
            "rationale": rationale,
        }

    def build_turn_outcome(
        self,
        *,
        turn_input: AssistantTurnInput,
        turn_plan: AssistantTurnPlan,
        tool_results: list[dict[str, object]],
        reflection_summary: str,
        partial_failures: list[dict[str, object]],
        next_action: str,
        next_directive: NextTurnDirective,
    ) -> AssistantTurnOutcome:
        unresolved_questions = self._unresolved_questions(
            turn_plan=turn_plan,
            tool_results=tool_results,
            partial_failures=partial_failures,
        )
        next_turn_hint = self._next_turn_hint(
            turn_plan=turn_plan,
            next_directive=next_directive,
            partial_failures=partial_failures,
        )
        carry_forward_context = self._carry_forward_context(
            reflection_summary=reflection_summary,
            tool_results=tool_results,
            partial_failures=partial_failures,
            next_directive=next_directive,
            next_turn_hint=next_turn_hint,
            unresolved_questions=unresolved_questions,
        )
        return AssistantTurnOutcome(
            turn_id=turn_input.turn_id,
            cycle_id=turn_input.cycle_id,
            resulting_directive=next_directive.value,
            next_turn_hint=next_turn_hint,
            unresolved_questions=unresolved_questions,
            carry_forward_context=carry_forward_context,
            next_action=next_action,
            executed_task_ids=self._executed_task_ids(tool_results),
            tool_result_count=len(tool_results),
            partial_failure_count=len(partial_failures),
            reflection_summary=reflection_summary,
        )

    def persist_bundle(
        self,
        *,
        mutable_state: dict[str, object],
        bundle: AssistantTurnBundle,
    ) -> AssistantTurnState:
        existing_state = AssistantTurnState.from_state(mutable_state.get("assistant_turn"))
        history = list(existing_state.history) if existing_state is not None else []
        history.append(
            {
                "input": bundle.turn_input.to_state(),
                "plan": bundle.turn_plan.to_state(),
                "outcome": bundle.turn_outcome.to_state(),
            }
        )
        state = AssistantTurnState(
            input=bundle.turn_input,
            plan=bundle.turn_plan,
            outcome=bundle.turn_outcome,
            history=history,
        )
        mutable_state["assistant_turn"] = state.to_state()
        return state

    def latest_outcome(self, mutable_state: dict[str, object]) -> AssistantTurnOutcome | None:
        existing_state = AssistantTurnState.from_state(mutable_state.get("assistant_turn"))
        if existing_state is None:
            return None
        return existing_state.outcome

    def apply_to_cycle(
        self,
        *,
        cycle: WorkflowCycleArtifact,
        bundle: AssistantTurnBundle,
    ) -> WorkflowCycleArtifact:
        return WorkflowCycleArtifact(
            cycle_id=cycle.cycle_id,
            batch_cycle=cycle.batch_cycle,
            selected_tasks=cycle.selected_tasks,
            scheduler_mode=cycle.scheduler_mode,
            parallel_read_group=cycle.parallel_read_group,
            serialized_write_group=cycle.serialized_write_group,
            scheduler_summary=cycle.scheduler_summary,
            merge_summary=cycle.merge_summary,
            partial_failures=cycle.partial_failures,
            retrieval_summary=cycle.retrieval_summary,
            retrieval=cycle.retrieval,
            tool_results=cycle.tool_results,
            reflection_summary=cycle.reflection_summary,
            memory_writes=cycle.memory_writes,
            memory=cycle.memory,
            compaction_summary=cycle.compaction_summary,
            context_projection=cycle.context_projection,
            assistant_turn_input=bundle.turn_input.to_state(),
            assistant_turn_plan=bundle.turn_plan.to_state(),
            assistant_turn_outcome=bundle.turn_outcome.to_state(),
            next_action=cycle.next_action,
            started_at=cycle.started_at,
            ended_at=cycle.ended_at,
        )

    @staticmethod
    def _retrieval_context(context_snapshot: ContextSnapshot) -> dict[str, object]:
        retrieval = context_snapshot.retrieval
        return {
            "summary": retrieval.summary,
            "session_local": {
                "summary": retrieval.session_local.summary,
                "status": retrieval.session_local.status,
                "source_count": retrieval.session_local.source_count,
            },
            "project": {
                "summary": retrieval.project.summary,
                "status": retrieval.project.status,
                "source_count": retrieval.project.source_count,
            },
            "capability": {
                "summary": retrieval.capability.summary,
                "status": retrieval.capability.status,
                "source_count": retrieval.capability.source_count,
            },
        }

    @staticmethod
    def _memory_context(context_snapshot: ContextSnapshot) -> dict[str, object]:
        memory = context_snapshot.memory
        return {
            "summary": memory.summary,
            "working": {
                "summary": memory.working.summary,
                "raw_count": len(memory.working.raw_entries),
                "distilled_count": len(memory.working.distilled_entries),
            },
            "session": {
                "summary": memory.session.summary,
                "raw_count": len(memory.session.raw_entries),
                "distilled_count": len(memory.session.distilled_entries),
            },
            "project": {
                "summary": memory.project.summary,
                "raw_count": len(memory.project.raw_entries),
                "distilled_count": len(memory.project.distilled_entries),
            },
            "promotion_count": len(memory.promotions),
            "demotion_count": len(memory.demotions),
        }

    @staticmethod
    def _transcript_context(
        *,
        context_snapshot: ContextSnapshot,
        prior_outcome: AssistantTurnOutcome | None,
    ) -> dict[str, object]:
        prompting = (
            context_snapshot.prompting if isinstance(context_snapshot.prompting, dict) else {}
        )
        continuity = prompting.get("continuity")
        continuity_dict = continuity if isinstance(continuity, dict) else {}
        raw_recent_delta_ids = continuity_dict.get("recent_delta_ids")
        raw_tool_result_delta_ids = continuity_dict.get("tool_result_delta_ids")
        recent_delta_ids = (
            [item for item in raw_recent_delta_ids if isinstance(item, str)]
            if isinstance(raw_recent_delta_ids, list)
            else []
        )
        tool_result_delta_ids = (
            [item for item in raw_tool_result_delta_ids if isinstance(item, str)]
            if isinstance(raw_tool_result_delta_ids, list)
            else []
        )
        return {
            "source": str(continuity_dict.get("source") or "runtime_transcript"),
            "recent_delta_ids": recent_delta_ids,
            "tool_result_delta_ids": tool_result_delta_ids,
            "recent_delta_count": len(recent_delta_ids),
            "prior_carry_forward_context": (
                prior_outcome.carry_forward_context if prior_outcome is not None else ""
            ),
            "prior_resulting_directive": (
                prior_outcome.resulting_directive if prior_outcome is not None else "continue"
            ),
            "prior_next_turn_hint": (
                prior_outcome.next_turn_hint if prior_outcome is not None else ""
            ),
        }

    @staticmethod
    def _reasoning_frame(
        *,
        context_snapshot: ContextSnapshot,
        prior_outcome: AssistantTurnOutcome | None,
        schedule: WorkflowToolSchedule,
    ) -> dict[str, object]:
        return {
            "projection_summary": context_snapshot.projection.summary,
            "last_directive": (
                prior_outcome.resulting_directive if prior_outcome is not None else "continue"
            ),
            "carry_forward_context": (
                prior_outcome.carry_forward_context if prior_outcome is not None else ""
            ),
            "scheduler_mode": schedule.scheduler_mode,
            "selected_task_count": len(schedule.selected_tasks),
        }

    @staticmethod
    def _executed_task_ids(tool_results: list[dict[str, object]]) -> list[str]:
        result: list[str] = []
        for item in tool_results:
            task_id = item.get("task_id")
            if isinstance(task_id, str):
                result.append(task_id)
        return result

    @staticmethod
    def _unresolved_questions(
        *,
        turn_plan: AssistantTurnPlan,
        tool_results: list[dict[str, object]],
        partial_failures: list[dict[str, object]],
    ) -> list[str]:
        questions: list[str] = []
        if not tool_results and turn_plan.selected_task_names:
            selected_names = ", ".join(turn_plan.selected_task_names)
            questions.append(f"Why did the recommended wave not execute for {selected_names}?")
        for item in partial_failures:
            task_name = item.get("task_name")
            reason = item.get("reason")
            if isinstance(task_name, str) and isinstance(reason, str):
                questions.append(f"What follow-up is needed for {task_name}: {reason}?")
        return questions

    @staticmethod
    def _next_turn_hint(
        *,
        turn_plan: AssistantTurnPlan,
        next_directive: NextTurnDirective,
        partial_failures: list[dict[str, object]],
    ) -> str:
        if next_directive is NextTurnDirective.AWAIT_APPROVAL:
            return "Pause the next turn on the current workflow wave until approval is granted."
        if next_directive is NextTurnDirective.AWAIT_USER_INPUT:
            return "Pause the next turn until the workflow receives the required user input."
        if next_directive is NextTurnDirective.REPLAN_SUBGRAPH:
            return "Replan the next turn around failed or contradicted workflow hypotheses."
        if partial_failures:
            return "Continue the next turn with retries or follow-up checks for partial failures."
        expected_task_names = turn_plan.recommended_tool_wave.get("expected_task_names")
        if isinstance(expected_task_names, list) and expected_task_names:
            names = ", ".join(item for item in expected_task_names if isinstance(item, str))
            return f"Continue the next turn by advancing the workflow-selected wave: {names}."
        return "Continue the next turn from the current workflow frontier."

    @staticmethod
    def _carry_forward_context(
        *,
        reflection_summary: str,
        tool_results: list[dict[str, object]],
        partial_failures: list[dict[str, object]],
        next_directive: NextTurnDirective,
        next_turn_hint: str,
        unresolved_questions: list[str],
    ) -> str:
        executed_names = [
            str(item.get("task_name"))
            for item in tool_results
            if isinstance(item.get("task_name"), str)
        ]
        lines = [f"Resulting directive: {next_directive.value}.", next_turn_hint]
        if executed_names:
            lines.append(f"Executed workflow tasks: {', '.join(executed_names)}.")
        if reflection_summary.strip():
            lines.append(reflection_summary.strip())
        if unresolved_questions:
            lines.append(f"Open questions: {' | '.join(unresolved_questions)}")
        if partial_failures:
            failed_names = [
                str(item.get("task_name"))
                for item in partial_failures
                if isinstance(item.get("task_name"), str)
            ]
            if failed_names:
                lines.append(f"Partial failures: {', '.join(failed_names)}.")
        return " ".join(line for line in lines if line)

    @staticmethod
    def _string(raw: object) -> str | None:
        return raw if isinstance(raw, str) else None
