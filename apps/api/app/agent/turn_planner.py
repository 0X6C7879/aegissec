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
        workspace_context = self._workspace_state_from_snapshot(context_snapshot)
        pending_protocol_context = self._pending_protocol_context(workspace_context)
        recall_focus = self._recall_focus(context_snapshot, workspace_context)
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
            workspace_context=workspace_context,
            pending_protocol_context=pending_protocol_context,
            unresolved_questions_seed=self.build_unresolved_questions_seed(
                workspace_context=workspace_context,
                recall_focus=recall_focus,
                prior_outcome=prior_outcome,
                context_snapshot=context_snapshot,
            ),
            recall_focus=recall_focus,
            prior_turn_outcome_summary=self._prior_turn_outcome_summary(prior_outcome),
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
        wave_priority = self.choose_wave_priority(turn_input=turn_input, schedule=schedule)
        rationale = str(recommended_tool_wave.get("rationale") or "")
        return AssistantTurnPlan(
            turn_id=turn_input.turn_id,
            cycle_id=turn_input.cycle_id,
            recommended_tool_wave=recommended_tool_wave,
            scheduler_mode=schedule.scheduler_mode,
            selected_task_ids=selected_task_ids,
            selected_task_names=selected_task_names,
            wave_priority=wave_priority,
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
        turn_focus = self.build_turn_focus(
            turn_input=turn_input,
            turn_plan=turn_plan,
            partial_failures=partial_failures,
            next_directive=next_directive,
        )
        unresolved_questions = self.build_unresolved_questions(
            turn_input=turn_input,
            turn_plan=turn_plan,
            tool_results=tool_results,
            partial_failures=partial_failures,
        )
        next_turn_hint = self.build_next_turn_hint(
            turn_focus=turn_focus,
            turn_input=turn_input,
            turn_plan=turn_plan,
            next_directive=next_directive,
            partial_failures=partial_failures,
        )
        resume_strategy = self._resume_strategy(
            turn_input=turn_input,
            turn_focus=turn_focus,
            next_directive=next_directive,
        )
        carry_forward_context = self.build_carry_forward_context(
            turn_input=turn_input,
            turn_focus=turn_focus,
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
            turn_focus=turn_focus,
            resume_strategy=resume_strategy,
            recall_focus=dict(turn_input.recall_focus),
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
            "workspace_state": AssistantTurnPlanner._workspace_state(continuity_dict),
        }

    @staticmethod
    def _reasoning_frame(
        *,
        context_snapshot: ContextSnapshot,
        prior_outcome: AssistantTurnOutcome | None,
        schedule: WorkflowToolSchedule,
    ) -> dict[str, object]:
        prompting = (
            context_snapshot.prompting if isinstance(context_snapshot.prompting, dict) else {}
        )
        continuity = prompting.get("continuity") if isinstance(prompting, dict) else {}
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
            "workspace_state": AssistantTurnPlanner._workspace_state(continuity),
        }

    @staticmethod
    def _executed_task_ids(tool_results: list[dict[str, object]]) -> list[str]:
        result: list[str] = []
        for item in tool_results:
            task_id = item.get("task_id")
            if isinstance(task_id, str):
                result.append(task_id)
        return result

    def build_unresolved_questions(
        self,
        *,
        turn_input: AssistantTurnInput,
        turn_plan: AssistantTurnPlan,
        tool_results: list[dict[str, object]],
        partial_failures: list[dict[str, object]],
    ) -> list[str]:
        questions = list(turn_input.unresolved_questions_seed)
        if not tool_results and turn_plan.selected_task_names:
            selected_names = ", ".join(turn_plan.selected_task_names)
            questions.append(f"Why did the recommended wave not execute for {selected_names}?")
        for item in partial_failures:
            task_name = item.get("task_name")
            reason = item.get("reason")
            if isinstance(task_name, str) and isinstance(reason, str):
                questions.append(f"What follow-up is needed for {task_name}: {reason}?")
        return questions[:6]

    def build_next_turn_hint(
        self,
        *,
        turn_focus: dict[str, object],
        turn_input: AssistantTurnInput,
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
        focus_type = str(turn_focus.get("focus_type") or "")
        if focus_type == "pending_protocol":
            pending_kind = str(turn_input.pending_protocol_context.get("kind") or "protocol")
            return (
                f"Hold the next turn on the {pending_kind} protocol until the resume "
                "condition is met."
            )
        if focus_type == "investigation":
            recall_focus = str(turn_input.recall_focus.get("focus") or "current evidence")
            return f"Use the next turn to investigate unresolved evidence around {recall_focus}."
        expected_task_names = turn_plan.recommended_tool_wave.get("expected_task_names")
        if isinstance(expected_task_names, list) and expected_task_names:
            names = ", ".join(item for item in expected_task_names if isinstance(item, str))
            return f"Continue the next turn by advancing the workflow-selected wave: {names}."
        return "Continue the next turn from the current workflow frontier."

    def build_carry_forward_context(
        self,
        *,
        turn_input: AssistantTurnInput,
        turn_focus: dict[str, object],
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
        lines = [
            f"Resulting directive: {next_directive.value}.",
            f"Turn focus: {str(turn_focus.get('summary') or 'workflow progression')}",
            next_turn_hint,
        ]
        if executed_names:
            lines.append(f"Executed workflow tasks: {', '.join(executed_names)}.")
        if reflection_summary.strip():
            lines.append(reflection_summary.strip())
        if unresolved_questions:
            lines.append(f"Open questions: {' | '.join(unresolved_questions)}")
        recall_focus = str(turn_input.recall_focus.get("focus") or "")
        if recall_focus:
            lines.append(f"Recall focus: {recall_focus}.")
        if partial_failures:
            failed_names = [
                str(item.get("task_name"))
                for item in partial_failures
                if isinstance(item.get("task_name"), str)
            ]
            if failed_names:
                lines.append(f"Partial failures: {', '.join(failed_names)}.")
        return " ".join(line for line in lines if line)

    def build_turn_focus(
        self,
        *,
        turn_input: AssistantTurnInput,
        turn_plan: AssistantTurnPlan,
        partial_failures: list[dict[str, object]],
        next_directive: NextTurnDirective,
    ) -> dict[str, object]:
        if turn_input.pending_protocol_context.get("kind"):
            pending_kind = str(turn_input.pending_protocol_context.get("kind") or "protocol")
            return {
                "focus_type": "pending_protocol",
                "summary": (
                    f"Wait on {pending_kind} for "
                    f"{str(turn_input.pending_protocol_context.get('task_name') or 'workflow')}"
                ),
                "wave_priority": "resume",
            }
        if partial_failures:
            return {
                "focus_type": "stabilization",
                "summary": (
                    "Stabilize the workflow around partial failures and contradictory evidence."
                ),
                "wave_priority": "stabilize",
            }
        if turn_input.unresolved_questions_seed:
            return {
                "focus_type": "investigation",
                "summary": turn_input.unresolved_questions_seed[0],
                "wave_priority": "investigate",
            }
        return {
            "focus_type": "workflow_wave",
            "summary": (
                f"Advance the recommended workflow wave with priority {turn_plan.wave_priority}."
            ),
            "wave_priority": turn_plan.wave_priority,
            "directive": next_directive.value,
        }

    def choose_wave_priority(
        self, *, turn_input: AssistantTurnInput, schedule: WorkflowToolSchedule
    ) -> str:
        if turn_input.pending_protocol_context.get("kind"):
            return "resume"
        if turn_input.unresolved_questions_seed:
            return "investigate"
        if schedule.serialized_write_group:
            return "advance"
        return "stabilize"

    def build_unresolved_questions_seed(
        self,
        *,
        workspace_context: dict[str, object],
        recall_focus: dict[str, object],
        prior_outcome: AssistantTurnOutcome | None,
        context_snapshot: ContextSnapshot,
    ) -> list[str]:
        questions: list[str] = []
        pending_protocol = workspace_context.get("pending_protocol")
        pending_kind = (
            str(pending_protocol.get("kind") or "") if isinstance(pending_protocol, dict) else ""
        )
        if pending_kind:
            questions.append(
                f"What information is still needed to satisfy the pending {pending_kind}?"
            )
        selected_memory = workspace_context.get("selected_project_memory_entries")
        if isinstance(selected_memory, list) and selected_memory:
            questions.append(
                "Which prior project memory best explains the current focus on "
                f"{selected_memory[0]}?"
            )
        recall_target = str(recall_focus.get("focus") or "")
        if recall_target:
            questions.append(f"What evidence should be recalled next for {recall_target}?")
        if prior_outcome is not None and prior_outcome.unresolved_questions:
            questions.extend(prior_outcome.unresolved_questions[:2])
        transcript_recent = (
            context_snapshot.prompting.get("continuity", {})
            if isinstance(context_snapshot.prompting, dict)
            else {}
        )
        if isinstance(transcript_recent, dict) and not questions:
            recent_delta_ids = transcript_recent.get("recent_delta_ids")
            if isinstance(recent_delta_ids, list) and recent_delta_ids:
                questions.append(
                    "What changed in the latest transcript continuity that should shape "
                    "the next turn?"
                )
        return questions[:4]

    def _resume_strategy(
        self,
        *,
        turn_input: AssistantTurnInput,
        turn_focus: dict[str, object],
        next_directive: NextTurnDirective,
    ) -> dict[str, object]:
        pending_kind = str(turn_input.pending_protocol_context.get("kind") or "")
        if next_directive is NextTurnDirective.AWAIT_USER_INPUT:
            return {
                "mode": "await_user_input",
                "resume_condition": str(
                    turn_input.pending_protocol_context.get("resume_condition") or ""
                ),
                "pending_kind": pending_kind,
            }
        if next_directive is NextTurnDirective.AWAIT_APPROVAL:
            return {
                "mode": "await_approval",
                "resume_condition": str(
                    turn_input.pending_protocol_context.get("resume_condition") or ""
                ),
                "pending_kind": pending_kind,
            }
        if next_directive is NextTurnDirective.REPLAN_SUBGRAPH:
            return {"mode": "replan", "resume_condition": "rebuild the working hypothesis graph"}
        return {
            "mode": "continue_execution",
            "resume_condition": str(turn_focus.get("summary") or "continue current workflow focus"),
        }

    @staticmethod
    def _prior_turn_outcome_summary(prior_outcome: AssistantTurnOutcome | None) -> str:
        if prior_outcome is None:
            return ""
        return (
            f"Prior directive {prior_outcome.resulting_directive}; "
            f"hint {prior_outcome.next_turn_hint}; "
            f"carry {prior_outcome.carry_forward_context}"
        )[:400]

    @staticmethod
    def _workspace_state_from_snapshot(context_snapshot: ContextSnapshot) -> dict[str, object]:
        prompting = (
            context_snapshot.prompting if isinstance(context_snapshot.prompting, dict) else {}
        )
        continuity = prompting.get("continuity") if isinstance(prompting, dict) else {}
        return AssistantTurnPlanner._workspace_state(continuity)

    @staticmethod
    def _pending_protocol_context(workspace_context: dict[str, object]) -> dict[str, object]:
        pending = workspace_context.get("pending_protocol")
        pending_dict = (
            {str(key): value for key, value in pending.items()} if isinstance(pending, dict) else {}
        )
        return {
            "kind": str(pending_dict.get("kind") or ""),
            "pause_reason": str(pending_dict.get("pause_reason") or ""),
            "resume_condition": str(pending_dict.get("resume_condition") or ""),
            "task_id": str(pending_dict.get("task_id") or ""),
            "task_name": str(pending_dict.get("task_name") or ""),
        }

    @staticmethod
    def _recall_focus(
        context_snapshot: ContextSnapshot, workspace_context: dict[str, object]
    ) -> dict[str, object]:
        retrieval_focus = workspace_context.get("active_retrieval_focus")
        if isinstance(retrieval_focus, dict):
            return {str(key): value for key, value in retrieval_focus.items()}
        retrieval = context_snapshot.retrieval
        if retrieval.project.items:
            first = retrieval.project.items[0]
            return {
                "scope": "project",
                "focus": first.record_id,
                "source_count": retrieval.project.source_count,
            }
        if retrieval.session_local.items:
            first = retrieval.session_local.items[0]
            return {
                "scope": "session_local",
                "focus": first.record_id,
                "source_count": retrieval.session_local.source_count,
            }
        return {"scope": "", "focus": "", "source_count": 0}

    @staticmethod
    def _string(raw: object) -> str | None:
        return raw if isinstance(raw, str) else None

    @staticmethod
    def _workspace_state(continuity: object) -> dict[str, object]:
        if not isinstance(continuity, dict):
            return {}
        workspace_rehydrate = continuity.get("workspace_rehydrate")
        if isinstance(workspace_rehydrate, dict):
            state = workspace_rehydrate.get("state")
            if isinstance(state, dict):
                return {str(key): value for key, value in state.items()}
        workspace_state = continuity.get("workspace_state")
        if isinstance(workspace_state, dict):
            return {str(key): value for key, value in workspace_state.items()}
        return {}
