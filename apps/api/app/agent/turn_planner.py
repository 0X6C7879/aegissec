from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from app.agent.assistant_runtime import AssistantExecutionRuntime, AssistantRuntimeService
from app.agent.assistant_turn import (
    AssistantTurnInput,
    AssistantTurnOutcome,
    AssistantTurnPlan,
    AssistantTurnState,
)
from app.agent.context_models import ContextSnapshot
from app.agent.loop_models import WorkflowCycleArtifact
from app.agent.selection import RunnableSelection, SelectedTask
from app.agent.tool_scheduler import WorkflowToolSchedule
from app.agent.tool_wave import (
    ToolWaveCandidate,
    ToolWaveDecision,
)
from app.agent.turn_models import NextTurnDirective


@dataclass(frozen=True)
class AssistantTurnBundle:
    turn_input: AssistantTurnInput
    turn_plan: AssistantTurnPlan
    turn_outcome: AssistantTurnOutcome


class AssistantTurnPlanner:
    def __init__(self) -> None:
        self._assistant_runtime = AssistantRuntimeService()

    def build_execution_runtime(
        self,
        *,
        cycle_id: str,
        mutable_state: dict[str, object],
        selection: RunnableSelection,
    ) -> AssistantExecutionRuntime:
        return self._assistant_runtime.build_runtime(
            cycle_id=cycle_id,
            mutable_state=mutable_state,
            selection=selection,
        )

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
        assistant_execution_runtime: AssistantExecutionRuntime | None = None,
        candidate_waves: list[dict[str, object]] | None = None,
        chosen_wave: dict[str, object] | None = None,
        wave_decision: dict[str, object] | None = None,
        assimilation_result: dict[str, object] | None = None,
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
            assistant_execution_runtime=assistant_execution_runtime,
            candidate_waves=(list(candidate_waves) if isinstance(candidate_waves, list) else None),
            chosen_wave=(dict(chosen_wave) if isinstance(chosen_wave, dict) else None),
            wave_decision=(dict(wave_decision) if isinstance(wave_decision, dict) else None),
        )
        resolved_assimilation_result = (
            dict(assimilation_result)
            if isinstance(assimilation_result, dict)
            else self.build_assimilation_result(
                mutable_state=mutable_state,
                tool_results=tool_results,
                partial_failures=partial_failures,
                next_directive=next_directive,
                chosen_wave=turn_plan.chosen_wave,
                assistant_execution_runtime=assistant_execution_runtime,
            )
        )
        turn_outcome = self.build_turn_outcome(
            turn_input=turn_input,
            turn_plan=turn_plan,
            tool_results=tool_results,
            reflection_summary=reflection_summary,
            partial_failures=partial_failures,
            next_action=next_action,
            next_directive=next_directive,
            assimilation_result=resolved_assimilation_result,
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
        assistant_execution_runtime: AssistantExecutionRuntime | None = None,
        candidate_waves: list[dict[str, object]] | None = None,
        chosen_wave: dict[str, object] | None = None,
        wave_decision: dict[str, object] | None = None,
    ) -> AssistantTurnPlan:
        selected_task_ids = [task.task_id for task in schedule.selected_tasks]
        selected_task_names = [task.task_name for task in schedule.selected_tasks]
        if assistant_execution_runtime is not None:
            execution_frame = assistant_execution_runtime.execution_frame
            resolved_candidate_waves = execution_frame.candidate_waves_state()
            resolved_chosen_wave = execution_frame.chosen_wave_state()
            resolved_wave_decision: dict[str, object] = execution_frame.wave_decision_state()
            recommended_tool_wave = self._recommended_tool_wave_from_runtime(
                assistant_execution_runtime=assistant_execution_runtime,
                schedule=schedule,
            )
        else:
            resolved_candidate_waves = (
                [
                    item.to_state() if isinstance(item, ToolWaveCandidate) else dict(item)
                    for item in candidate_waves
                    if isinstance(item, ToolWaveCandidate | dict)
                ]
                if isinstance(candidate_waves, list) and candidate_waves
                else [
                    item.to_state()
                    for item in self.build_candidate_waves(schedule=schedule, mutable_state={})
                ]
            )
            resolved_chosen_wave = (
                dict(chosen_wave)
                if isinstance(chosen_wave, dict) and chosen_wave
                else dict(resolved_candidate_waves[0])
                if resolved_candidate_waves
                else {}
            )
            resolved_wave_decision = (
                dict(wave_decision)
                if isinstance(wave_decision, dict)
                else {
                    "decision": "fallback_first_candidate",
                    "selected_wave_id": str(resolved_chosen_wave.get("wave_id") or ""),
                }
            )
            recommended_tool_wave = (
                self._recommended_tool_wave_from_chosen_wave(
                    chosen_wave=resolved_chosen_wave,
                    schedule=schedule,
                )
                if resolved_chosen_wave
                else self.resolve_turn_wave(schedule=schedule)
            )
        wave_priority = self.choose_wave_priority(turn_input=turn_input, schedule=schedule)
        rationale = str(recommended_tool_wave.get("rationale") or "")
        return AssistantTurnPlan(
            turn_id=turn_input.turn_id,
            cycle_id=turn_input.cycle_id,
            recommended_tool_wave=recommended_tool_wave,
            candidate_waves=resolved_candidate_waves,
            chosen_wave=resolved_chosen_wave,
            wave_decision=resolved_wave_decision,
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

    def _recommended_tool_wave_from_chosen_wave(
        self,
        *,
        chosen_wave: dict[str, object],
        schedule: WorkflowToolSchedule,
    ) -> dict[str, object]:
        task_ids = chosen_wave.get("task_ids")
        task_names = chosen_wave.get("task_names")
        metadata = chosen_wave.get("metadata")
        metadata_dict = (
            {str(key): value for key, value in metadata.items()}
            if isinstance(metadata, dict)
            else {}
        )
        expected_task_ids = (
            [item for item in task_ids if isinstance(item, str)]
            if isinstance(task_ids, list)
            else []
        )
        expected_task_names = (
            [item for item in task_names if isinstance(item, str)]
            if isinstance(task_names, list)
            else []
        )
        parallel_read_task_ids = metadata_dict.get("parallel_read_task_ids")
        serialized_write_task_ids = metadata_dict.get("serialized_write_task_ids")
        return {
            "wave_id": str(chosen_wave.get("wave_id") or ""),
            "wave_type": str(chosen_wave.get("wave_type") or "execution"),
            "scheduler_mode": schedule.scheduler_mode,
            "expected_task_ids": expected_task_ids,
            "expected_task_names": expected_task_names,
            "parallel_read_task_ids": (
                [item for item in parallel_read_task_ids if isinstance(item, str)]
                if isinstance(parallel_read_task_ids, list)
                else [task.task_id for task in schedule.parallel_read_group]
            ),
            "serialized_write_task_ids": (
                [item for item in serialized_write_task_ids if isinstance(item, str)]
                if isinstance(serialized_write_task_ids, list)
                else [task.task_id for task in schedule.serialized_write_group]
            ),
            "expected_wave_size": len(expected_task_ids),
            "rationale": str(chosen_wave.get("rationale") or ""),
        }

    def _recommended_tool_wave_from_runtime(
        self,
        *,
        assistant_execution_runtime: AssistantExecutionRuntime,
        schedule: WorkflowToolSchedule,
    ) -> dict[str, object]:
        chosen_wave = assistant_execution_runtime.execution_frame.chosen_wave
        chosen_execution_wave = assistant_execution_runtime.chosen_execution_wave
        metadata = dict(chosen_wave.metadata) if chosen_wave is not None else {}
        expected_task_ids = list(chosen_execution_wave.task_ids)
        expected_task_names = list(chosen_execution_wave.task_names)
        return {
            "wave_id": chosen_execution_wave.wave_id,
            "wave_type": chosen_wave.wave_type if chosen_wave is not None else "execution",
            "scheduler_mode": chosen_wave.scheduler_mode
            if chosen_wave is not None
            else schedule.scheduler_mode,
            "expected_task_ids": expected_task_ids,
            "expected_task_names": expected_task_names,
            "parallel_read_task_ids": self._compatibility_wave_group_task_ids(
                metadata=metadata,
                metadata_key="parallel_read_task_ids",
                chosen_task_ids=expected_task_ids,
                scheduled_group=schedule.parallel_read_group,
            ),
            "serialized_write_task_ids": self._compatibility_wave_group_task_ids(
                metadata=metadata,
                metadata_key="serialized_write_task_ids",
                chosen_task_ids=expected_task_ids,
                scheduled_group=schedule.serialized_write_group,
            ),
            "expected_wave_size": len(expected_task_ids),
            "rationale": chosen_wave.rationale if chosen_wave is not None else "",
        }

    @staticmethod
    def _compatibility_wave_group_task_ids(
        *,
        metadata: dict[str, object],
        metadata_key: str,
        chosen_task_ids: list[str],
        scheduled_group: list[SelectedTask],
    ) -> list[str]:
        raw_group = metadata.get(metadata_key)
        if isinstance(raw_group, list):
            return [item for item in raw_group if isinstance(item, str)]
        scheduled_task_ids = [task.task_id for task in scheduled_group]
        chosen_task_id_set = set(chosen_task_ids)
        return [task_id for task_id in scheduled_task_ids if task_id in chosen_task_id_set]

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
        assimilation_result: dict[str, object],
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
            assimilation_result=dict(assimilation_result),
        )

    def build_candidate_waves(
        self, *, schedule: WorkflowToolSchedule, mutable_state: dict[str, object]
    ) -> list[ToolWaveCandidate]:
        pending_kind = self._pending_kind(mutable_state)
        expected_task_ids = [task.task_id for task in schedule.selected_tasks]
        expected_task_names = [task.task_name for task in schedule.selected_tasks]
        candidate_waves: list[ToolWaveCandidate] = [
            ToolWaveCandidate(
                wave_id="workflow_selected_wave",
                wave_type="execution",
                scheduler_mode=schedule.scheduler_mode,
                task_ids=expected_task_ids,
                task_names=expected_task_names,
                rationale=(
                    "Primary assistant wave selected from workflow scheduler read/write phases."
                ),
                metadata={
                    "parallel_read_task_ids": [
                        task.task_id for task in schedule.parallel_read_group
                    ],
                    "serialized_write_task_ids": [
                        task.task_id for task in schedule.serialized_write_group
                    ],
                    "scheduler_group": "mixed",
                },
            )
        ]
        if schedule.parallel_read_group:
            candidate_waves.append(
                ToolWaveCandidate(
                    wave_id="recall_investigation_candidate",
                    wave_type="investigation",
                    scheduler_mode=schedule.scheduler_mode,
                    task_ids=[task.task_id for task in schedule.parallel_read_group],
                    task_names=[task.task_name for task in schedule.parallel_read_group],
                    rationale=(
                        "Recall-driven investigation candidate prioritizing read-only checks."
                    ),
                    metadata={"scheduler_group": "parallel_read_group"},
                )
            )
        if pending_kind:
            pending_token = self._pending_continuation_token(mutable_state)
            candidate_waves.append(
                ToolWaveCandidate(
                    wave_id="continuation_resume_candidate",
                    wave_type="continuation",
                    scheduler_mode=schedule.scheduler_mode,
                    task_ids=[],
                    task_names=[],
                    rationale=f"Continuation pending for protocol kind '{pending_kind}'.",
                    metadata={
                        "pending_kind": pending_kind,
                        "continuation_token": pending_token,
                        "scheduler_group": "continuation",
                    },
                )
            )
        if self._needs_stabilization_replan(mutable_state):
            candidate_waves.append(
                ToolWaveCandidate(
                    wave_id="stabilization_replan_candidate",
                    wave_type="stabilization",
                    scheduler_mode=schedule.scheduler_mode,
                    task_ids=[task.task_id for task in schedule.parallel_read_group],
                    task_names=[task.task_name for task in schedule.parallel_read_group],
                    rationale=(
                        "Partial failures or contradictions detected; prefer stabilization/"
                        "replan wave."
                    ),
                    metadata={
                        "scheduler_group": "parallel_read_group",
                        "trigger": "partial_failure_or_contradiction",
                    },
                )
            )
        if schedule.parallel_read_group:
            candidate_waves.append(
                ToolWaveCandidate(
                    wave_id="read_stabilization",
                    wave_type="stabilization",
                    scheduler_mode=schedule.scheduler_mode,
                    task_ids=[task.task_id for task in schedule.parallel_read_group],
                    task_names=[task.task_name for task in schedule.parallel_read_group],
                    rationale="Read-only stabilization wave when writes should be deferred.",
                    metadata={"scheduler_group": "parallel_read_group"},
                )
            )
        candidate_waves.append(
            ToolWaveCandidate(
                wave_id="continuation_hold",
                wave_type="continuation",
                scheduler_mode=schedule.scheduler_mode,
                task_ids=[],
                task_names=[],
                rationale=(
                    f"Hold execution while continuation kind '{pending_kind}' remains unresolved."
                    if pending_kind
                    else "Hold execution when no runnable wave is selected."
                ),
                metadata={"scheduler_group": "continuation"},
            )
        )
        return candidate_waves

    def choose_wave(
        self,
        *,
        schedule: WorkflowToolSchedule,
        mutable_state: dict[str, object],
        candidate_waves: list[ToolWaveCandidate],
    ) -> tuple[ToolWaveCandidate | None, ToolWaveDecision]:
        pending_kind = self._pending_kind(mutable_state)
        if pending_kind:
            hold = next(
                (
                    item
                    for item in candidate_waves
                    if item.wave_id == "continuation_resume_candidate"
                ),
                None,
            )
            selected = hold or next(
                (item for item in candidate_waves if item.wave_id == "continuation_hold"), None
            )
            return selected, ToolWaveDecision(
                decision="hold_for_continuation",
                selected_wave_id=selected.wave_id if selected is not None else "",
                reason=f"pending continuation kind={pending_kind}",
                confidence=1.0,
            )
        if self._needs_stabilization_replan(mutable_state):
            stabilization = next(
                (
                    item
                    for item in candidate_waves
                    if item.wave_id == "stabilization_replan_candidate"
                ),
                None,
            )
            if stabilization is not None:
                return stabilization, ToolWaveDecision(
                    decision="stabilize_before_advance",
                    selected_wave_id=stabilization.wave_id,
                    reason="partial failures/contradictions detected",
                    confidence=0.9,
                )
        primary = next(
            (item for item in candidate_waves if item.wave_id == "workflow_selected_wave"),
            None,
        )
        if primary is not None and primary.task_ids:
            return primary, ToolWaveDecision(
                decision="advance_primary_wave",
                selected_wave_id=primary.wave_id,
                reason=(
                    f"scheduler selected {len(primary.task_ids)} task(s) with "
                    f"mode {schedule.scheduler_mode}"
                ),
                confidence=0.8,
            )
        recall = next(
            (item for item in candidate_waves if item.wave_id == "recall_investigation_candidate"),
            None,
        )
        if recall is not None and recall.task_ids:
            return recall, ToolWaveDecision(
                decision="investigate_with_recall_wave",
                selected_wave_id=recall.wave_id,
                reason="primary wave empty; using recall-driven read wave",
                confidence=0.7,
            )
        fallback = next(
            (item for item in candidate_waves if item.wave_id == "read_stabilization"),
            None,
        )
        return fallback, ToolWaveDecision(
            decision="fallback_stabilization_wave",
            selected_wave_id=fallback.wave_id if fallback is not None else "",
            reason="primary wave is empty; fallback to stabilization/read wave",
            confidence=0.5,
        )

    def build_assimilation_result(
        self,
        *,
        mutable_state: dict[str, object],
        tool_results: list[dict[str, object]],
        partial_failures: list[dict[str, object]],
        next_directive: NextTurnDirective,
        chosen_wave: dict[str, object] | None = None,
        assistant_execution_runtime: AssistantExecutionRuntime | None = None,
        carry_forward_context: str = "",
    ) -> dict[str, object]:
        candidate = (
            assistant_execution_runtime.execution_frame.chosen_wave
            if assistant_execution_runtime is not None
            else ToolWaveCandidate.from_state(chosen_wave)
        )
        result = self._assistant_runtime.assimilate_wave_result(
            mutable_state=mutable_state,
            chosen_wave=candidate,
            tool_results=tool_results,
            partial_failures=partial_failures,
            next_directive=next_directive,
            carry_forward_context=carry_forward_context,
        )
        return result.to_state()

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
            candidate_waves=list(cycle.candidate_waves),
            chosen_wave=dict(cycle.chosen_wave),
            wave_decision=dict(cycle.wave_decision),
            assimilation_result=dict(cycle.assimilation_result),
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
        workbench_runtime = continuity.get("workbench_runtime")
        if isinstance(workbench_runtime, dict):
            state = workbench_runtime.get("state")
            if isinstance(state, dict):
                normalized = {str(key): value for key, value in state.items()}
                normalized.setdefault(
                    "latest_turn_directive",
                    str(normalized.get("latest_directive") or "continue"),
                )
                normalized.setdefault(
                    "pending_protocol",
                    {str(key): value for key, value in pending_protocol_summary.items()}
                    if isinstance(
                        (pending_protocol_summary := normalized.get("pending_protocol_summary")),
                        dict,
                    )
                    else {},
                )
                normalized.setdefault(
                    "selected_project_memory_entries",
                    [item for item in active_memory_selection if isinstance(item, str)]
                    if isinstance(
                        (active_memory_selection := normalized.get("active_memory_selection")), list
                    )
                    else [],
                )
                normalized.setdefault(
                    "active_retrieval_focus",
                    {str(key): value for key, value in active_recall_focus.items()}
                    if isinstance(
                        (active_recall_focus := normalized.get("active_recall_focus")), dict
                    )
                    else {},
                )
                return normalized
        workspace_rehydrate = continuity.get("workspace_rehydrate")
        if isinstance(workspace_rehydrate, dict):
            state = workspace_rehydrate.get("state")
            if isinstance(state, dict):
                return {str(key): value for key, value in state.items()}
        workspace_state = continuity.get("workspace_state")
        if isinstance(workspace_state, dict):
            return {str(key): value for key, value in workspace_state.items()}
        return {}

    @staticmethod
    def _pending_continuation_token(mutable_state: dict[str, object]) -> str | None:
        pause = mutable_state.get("pause")
        if not isinstance(pause, dict):
            return None
        active = pause.get("active")
        if not isinstance(active, dict):
            return None
        token = active.get("continuation_token")
        return token if isinstance(token, str) and token else None

    @staticmethod
    def _workbench_focus(mutable_state: dict[str, object]) -> dict[str, object]:
        runtime = mutable_state.get("workbench_runtime")
        if not isinstance(runtime, dict):
            return {}
        state = runtime.get("state")
        if not isinstance(state, dict):
            return {}
        focus = state.get("active_recall_focus")
        return {str(key): value for key, value in focus.items()} if isinstance(focus, dict) else {}

    @staticmethod
    def _needs_stabilization_replan(mutable_state: dict[str, object]) -> bool:
        loop = mutable_state.get("loop")
        if isinstance(loop, dict):
            cycles = loop.get("cycles")
            if isinstance(cycles, list) and cycles:
                latest_cycle = cycles[-1]
                if isinstance(latest_cycle, dict):
                    partial_failures = latest_cycle.get("partial_failures")
                    if isinstance(partial_failures, list) and partial_failures:
                        return True
        replan_records = mutable_state.get("replan_records")
        return isinstance(replan_records, list) and bool(replan_records)

    @staticmethod
    def _pending_kind(mutable_state: dict[str, object]) -> str:
        pause = mutable_state.get("pause")
        if not isinstance(pause, dict):
            return ""
        active = pause.get("active")
        if not isinstance(active, dict):
            return ""
        return str(active.get("kind") or "")
