from __future__ import annotations

from dataclasses import dataclass, field

from app.agent.selection import RunnableSelection
from app.agent.tool_scheduler import SCHEDULER_MODE
from app.agent.tool_wave import (
    AssistantExecutionFrame,
    ToolWaveCandidate,
    ToolWaveDecision,
    ToolWaveExecutionFrame,
    TurnAssimilationResult,
)
from app.agent.turn_models import NextTurnDirective


@dataclass(frozen=True)
class AssistantExecutionContext:
    frame_id: str
    cycle_id: str
    stage: str | None
    goal: str
    active_task_ids: list[str] = field(default_factory=list)
    active_task_names: list[str] = field(default_factory=list)
    pending_continuation_token: str | None = None
    active_recall_focus: dict[str, object] = field(default_factory=dict)
    metadata: dict[str, object] = field(default_factory=dict)

    def to_state(self) -> dict[str, object]:
        return {
            "frame_id": self.frame_id,
            "cycle_id": self.cycle_id,
            "stage": self.stage,
            "goal": self.goal,
            "active_task_ids": list(self.active_task_ids),
            "active_task_names": list(self.active_task_names),
            "pending_continuation_token": self.pending_continuation_token,
            "active_recall_focus": dict(self.active_recall_focus),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_state(cls, raw: object) -> AssistantExecutionContext | None:
        if not isinstance(raw, dict):
            return None
        frame_id = raw.get("frame_id")
        cycle_id = raw.get("cycle_id")
        goal = raw.get("goal")
        if (
            not isinstance(frame_id, str)
            or not isinstance(cycle_id, str)
            or not isinstance(goal, str)
        ):
            return None
        stage = raw.get("stage")
        active_task_ids = raw.get("active_task_ids")
        active_task_names = raw.get("active_task_names")
        pending_continuation_token = raw.get("pending_continuation_token")
        active_recall_focus = raw.get("active_recall_focus")
        metadata = raw.get("metadata")
        return cls(
            frame_id=frame_id,
            cycle_id=cycle_id,
            stage=stage if isinstance(stage, str) else None,
            goal=goal,
            active_task_ids=[item for item in active_task_ids if isinstance(item, str)]
            if isinstance(active_task_ids, list)
            else [],
            active_task_names=[item for item in active_task_names if isinstance(item, str)]
            if isinstance(active_task_names, list)
            else [],
            pending_continuation_token=(
                pending_continuation_token
                if isinstance(pending_continuation_token, str) and pending_continuation_token
                else None
            ),
            active_recall_focus=(
                {str(key): value for key, value in active_recall_focus.items()}
                if isinstance(active_recall_focus, dict)
                else {}
            ),
            metadata=(
                {str(key): value for key, value in metadata.items()}
                if isinstance(metadata, dict)
                else {}
            ),
        )


@dataclass(frozen=True)
class AssistantExecutionRuntime:
    execution_context: AssistantExecutionContext
    execution_frame: AssistantExecutionFrame
    chosen_execution_wave: ToolWaveExecutionFrame

    def to_state(self) -> dict[str, object]:
        return {
            "execution_context": self.execution_context.to_state(),
            "execution_frame": self.execution_frame.to_state(),
            "chosen_execution_wave": self.chosen_execution_wave.to_state(),
        }

    @classmethod
    def from_state(cls, raw: object) -> AssistantExecutionRuntime | None:
        if not isinstance(raw, dict):
            return None
        execution_context = AssistantExecutionContext.from_state(raw.get("execution_context"))
        execution_frame = AssistantExecutionFrame.from_state(raw.get("execution_frame"))
        chosen_execution_wave = ToolWaveExecutionFrame.from_state(raw.get("chosen_execution_wave"))
        if execution_context is None or execution_frame is None or chosen_execution_wave is None:
            return None
        return cls(
            execution_context=execution_context,
            execution_frame=execution_frame,
            chosen_execution_wave=chosen_execution_wave,
        )


class AssistantRuntimeService:
    def build_runtime(
        self,
        *,
        cycle_id: str,
        mutable_state: dict[str, object],
        selection: RunnableSelection,
    ) -> AssistantExecutionRuntime:
        candidate_waves = self.build_candidate_waves(
            mutable_state=mutable_state,
            selection=selection,
        )
        chosen_wave, wave_decision = self.choose_preferred_wave(
            mutable_state=mutable_state,
            candidate_waves=candidate_waves,
        )
        frame = AssistantExecutionFrame(
            frame_id=f"assistant-frame-{cycle_id}",
            cycle_id=cycle_id,
            candidate_waves=candidate_waves,
            chosen_wave=chosen_wave,
            wave_decision=wave_decision,
        )
        chosen_execution_wave = ToolWaveExecutionFrame(
            wave_id=chosen_wave.wave_id if chosen_wave is not None else "",
            task_ids=list(chosen_wave.task_ids) if chosen_wave is not None else [],
            task_names=list(chosen_wave.task_names) if chosen_wave is not None else [],
            scheduler_group=(
                str(chosen_wave.metadata.get("scheduler_group") or "") if chosen_wave else None
            ),
            mode="execute" if chosen_wave is not None and chosen_wave.task_ids else "hold",
        )
        objective = self.build_turn_objective(
            mutable_state=mutable_state,
            chosen_wave=chosen_wave,
        )
        return AssistantExecutionRuntime(
            execution_context=AssistantExecutionContext(
                frame_id=frame.frame_id,
                cycle_id=cycle_id,
                stage=_string(mutable_state.get("current_stage")),
                goal=str(mutable_state.get("goal") or "authorized assessment"),
                active_task_ids=[task.task_id for task in selection.selected_tasks],
                active_task_names=[task.task_name for task in selection.selected_tasks],
                pending_continuation_token=self._pending_continuation_token(mutable_state),
                active_recall_focus=self._workbench_focus(mutable_state),
                metadata={
                    "selection_task_ids": [task.task_id for task in selection.selected_tasks],
                    "selection_task_names": [task.task_name for task in selection.selected_tasks],
                    "turn_objective": objective,
                },
            ),
            execution_frame=frame,
            chosen_execution_wave=chosen_execution_wave,
        )

    def build_candidate_waves(
        self,
        *,
        mutable_state: dict[str, object],
        selection: RunnableSelection,
    ) -> list[ToolWaveCandidate]:
        workflow_tasks = list(selection.selected_tasks)
        workflow_read_tasks = list(selection.parallel_read_group)
        workflow_write_tasks = list(selection.serialized_write_group)
        pending_kind = self._pending_kind(mutable_state)
        candidate_waves: list[ToolWaveCandidate] = [
            ToolWaveCandidate(
                wave_id="workflow_selected_wave",
                wave_type="execution",
                scheduler_mode=SCHEDULER_MODE,
                task_ids=[task.task_id for task in workflow_tasks],
                task_names=[task.task_name for task in workflow_tasks],
                rationale=(
                    "Assistant primary execution candidate derived from currently "
                    "runnable workflow tasks."
                ),
                metadata={
                    "parallel_read_task_ids": [task.task_id for task in workflow_read_tasks],
                    "serialized_write_task_ids": [task.task_id for task in workflow_write_tasks],
                    "scheduler_group": "mixed",
                    "candidate_source": "workflow_selector",
                },
            )
        ]
        if workflow_read_tasks:
            candidate_waves.append(
                ToolWaveCandidate(
                    wave_id="recall_investigation_candidate",
                    wave_type="investigation",
                    scheduler_mode=SCHEDULER_MODE,
                    task_ids=[task.task_id for task in workflow_read_tasks],
                    task_names=[task.task_name for task in workflow_read_tasks],
                    rationale=(
                        "Recall-driven investigation candidate that prefers read-only "
                        "evidence gathering."
                    ),
                    metadata={
                        "scheduler_group": "parallel_read_group",
                        "candidate_source": "workflow_selector",
                    },
                )
            )
        if pending_kind:
            candidate_waves.append(
                ToolWaveCandidate(
                    wave_id="continuation_resume_candidate",
                    wave_type="continuation",
                    scheduler_mode=SCHEDULER_MODE,
                    task_ids=[],
                    task_names=[],
                    rationale=(
                        f"Pending continuation for protocol kind '{pending_kind}' takes precedence."
                    ),
                    metadata={
                        "pending_kind": pending_kind,
                        "continuation_token": self._pending_continuation_token(mutable_state),
                        "scheduler_group": "continuation",
                        "candidate_source": "continuation_store",
                    },
                )
            )
        if self._needs_stabilization_replan(mutable_state):
            stabilization_tasks = workflow_read_tasks or workflow_tasks
            candidate_waves.append(
                ToolWaveCandidate(
                    wave_id="stabilization_replan_candidate",
                    wave_type="stabilization",
                    scheduler_mode=SCHEDULER_MODE,
                    task_ids=[task.task_id for task in stabilization_tasks],
                    task_names=[task.task_name for task in stabilization_tasks],
                    rationale=(
                        "Contradictions or partial failures detected; stabilize before "
                        "advancing writes."
                    ),
                    metadata={
                        "scheduler_group": (
                            "parallel_read_group"
                            if workflow_read_tasks
                            else "serialized_write_group"
                        ),
                        "candidate_source": "assistant_reflection",
                    },
                )
            )
        if not workflow_tasks:
            candidate_waves.append(
                ToolWaveCandidate(
                    wave_id="continuation_hold",
                    wave_type="continuation",
                    scheduler_mode=SCHEDULER_MODE,
                    task_ids=[],
                    task_names=[],
                    rationale="No runnable workflow tasks are currently available.",
                    metadata={
                        "scheduler_group": "continuation",
                        "candidate_source": "assistant_hold",
                    },
                )
            )
        return candidate_waves

    def choose_preferred_wave(
        self,
        *,
        mutable_state: dict[str, object],
        candidate_waves: list[ToolWaveCandidate],
    ) -> tuple[ToolWaveCandidate | None, ToolWaveDecision]:
        pending_kind = self._pending_kind(mutable_state)
        if pending_kind:
            selected = next(
                (
                    wave
                    for wave in candidate_waves
                    if wave.wave_id == "continuation_resume_candidate"
                ),
                None,
            )
            return selected, ToolWaveDecision(
                decision="resume_continuation_protocol",
                selected_wave_id=selected.wave_id if selected is not None else "",
                reason=f"pending continuation kind={pending_kind}",
                confidence=1.0,
                metadata={"pending_kind": pending_kind},
            )
        if self._needs_stabilization_replan(mutable_state):
            selected = next(
                (
                    wave
                    for wave in candidate_waves
                    if wave.wave_id == "stabilization_replan_candidate"
                ),
                None,
            )
            if selected is not None:
                return selected, ToolWaveDecision(
                    decision="stabilize_and_replan",
                    selected_wave_id=selected.wave_id,
                    reason="partial failures or contradictions require stabilization first",
                    confidence=0.92,
                )
        selected = next(
            (
                wave
                for wave in candidate_waves
                if wave.wave_id == "workflow_selected_wave" and wave.task_ids
            ),
            None,
        )
        if selected is not None:
            return selected, ToolWaveDecision(
                decision="advance_workflow_wave",
                selected_wave_id=selected.wave_id,
                reason=f"assistant selected {len(selected.task_ids)} runnable workflow task(s)",
                confidence=0.82,
            )
        selected = next(
            (
                wave
                for wave in candidate_waves
                if wave.wave_id == "recall_investigation_candidate" and wave.task_ids
            ),
            None,
        )
        if selected is not None:
            return selected, ToolWaveDecision(
                decision="investigate_with_recall_wave",
                selected_wave_id=selected.wave_id,
                reason="no safe primary wave; use read-only investigation candidate",
                confidence=0.7,
            )
        selected = next(
            (wave for wave in candidate_waves if wave.wave_id == "continuation_hold"),
            None,
        )
        return selected, ToolWaveDecision(
            decision="hold_without_wave",
            selected_wave_id=selected.wave_id if selected is not None else "",
            reason="assistant found no runnable wave worth executing",
            confidence=0.5,
        )

    def build_turn_objective(
        self,
        *,
        mutable_state: dict[str, object],
        chosen_wave: ToolWaveCandidate | None,
    ) -> dict[str, object]:
        return {
            "goal": str(mutable_state.get("goal") or "authorized assessment"),
            "stage": _string(mutable_state.get("current_stage")),
            "wave_id": chosen_wave.wave_id if chosen_wave is not None else "",
            "wave_type": chosen_wave.wave_type if chosen_wave is not None else "hold",
            "task_count": len(chosen_wave.task_ids) if chosen_wave is not None else 0,
            "rationale": chosen_wave.rationale if chosen_wave is not None else "",
        }

    def synthesize_open_questions(
        self,
        *,
        mutable_state: dict[str, object],
        tool_results: list[dict[str, object]],
        partial_failures: list[dict[str, object]],
    ) -> list[str]:
        open_questions: list[str] = []
        for failure in partial_failures:
            if not isinstance(failure, dict):
                continue
            task_name = str(failure.get("task_name") or failure.get("task_id") or "workflow-task")
            reason = str(failure.get("reason") or "execution_failed")
            open_questions.append(f"Resolve {task_name}: {reason}")
        if not open_questions:
            assistant_turn = mutable_state.get("assistant_turn")
            outcome = assistant_turn.get("outcome") if isinstance(assistant_turn, dict) else None
            unresolved = outcome.get("unresolved_questions") if isinstance(outcome, dict) else None
            if isinstance(unresolved, list):
                open_questions.extend(str(item) for item in unresolved if isinstance(item, str))
        if not open_questions and not tool_results:
            pending_kind = self._pending_kind(mutable_state)
            if pending_kind:
                open_questions.append(f"Await {pending_kind} continuation before advancing.")
        return open_questions[:6]

    def assimilate_wave_result(
        self,
        *,
        mutable_state: dict[str, object],
        chosen_wave: ToolWaveCandidate | None,
        tool_results: list[dict[str, object]],
        partial_failures: list[dict[str, object]],
        next_directive: NextTurnDirective,
        carry_forward_context: str = "",
    ) -> TurnAssimilationResult:
        open_questions = self.synthesize_open_questions(
            mutable_state=mutable_state,
            tool_results=tool_results,
            partial_failures=partial_failures,
        )
        executed_task_ids = [
            str(item.get("task_id") or "")
            for item in tool_results
            if isinstance(item, dict) and isinstance(item.get("task_id"), str)
        ]
        expected_task_count = len(chosen_wave.task_ids) if chosen_wave is not None else 0
        summary = (
            f"wave={chosen_wave.wave_id if chosen_wave is not None else 'none'} "
            f"executed={len(executed_task_ids)}/{expected_task_count} "
            f"failures={len(partial_failures)} directive={next_directive.value}"
        )
        open_question_summary = "; ".join(open_questions) if open_questions else "none"
        return TurnAssimilationResult(
            selected_wave_id=chosen_wave.wave_id if chosen_wave is not None else "",
            expected_task_count=len(chosen_wave.task_ids) if chosen_wave is not None else 0,
            executed_task_count=len(executed_task_ids),
            executed_task_ids=executed_task_ids,
            partial_failure_count=len(partial_failures),
            directive=next_directive.value,
            status="assimilated" if tool_results else "no_execution",
            summary=(
                f"{summary}; open_questions={open_question_summary}"
                + (f"; carry_forward={carry_forward_context}" if carry_forward_context else "")
            ),
        )

    @staticmethod
    def filter_selection_to_wave(
        selection: RunnableSelection,
        chosen_wave: ToolWaveCandidate | None,
    ) -> RunnableSelection:
        if chosen_wave is None:
            return RunnableSelection(batch_size=selection.batch_size, selected_tasks=[])
        chosen_task_ids = set(chosen_wave.task_ids)
        if not chosen_task_ids:
            return RunnableSelection(batch_size=selection.batch_size, selected_tasks=[])
        selected_tasks = [
            task for task in selection.selected_tasks if task.task_id in chosen_task_ids
        ]
        return RunnableSelection(
            batch_size=selection.batch_size,
            selected_tasks=selected_tasks,
            parallel_read_group=[
                task for task in selection.parallel_read_group if task.task_id in chosen_task_ids
            ],
            serialized_write_group=[
                task for task in selection.serialized_write_group if task.task_id in chosen_task_ids
            ],
        )

    @staticmethod
    def _pending_kind(mutable_state: dict[str, object]) -> str:
        pause = mutable_state.get("pause")
        active = pause.get("active") if isinstance(pause, dict) else None
        return str(active.get("kind") or "") if isinstance(active, dict) else ""

    @staticmethod
    def _pending_continuation_token(mutable_state: dict[str, object]) -> str | None:
        pause = mutable_state.get("pause")
        active = pause.get("active") if isinstance(pause, dict) else None
        token = active.get("continuation_token") if isinstance(active, dict) else None
        return token if isinstance(token, str) and token else None

    @staticmethod
    def _needs_stabilization_replan(mutable_state: dict[str, object]) -> bool:
        replan_records = mutable_state.get("replan_records")
        if isinstance(replan_records, list) and replan_records:
            return True
        assistant_turn = mutable_state.get("assistant_turn")
        outcome = assistant_turn.get("outcome") if isinstance(assistant_turn, dict) else None
        partial_failure_count = (
            outcome.get("partial_failure_count") if isinstance(outcome, dict) else 0
        )
        return isinstance(partial_failure_count, int) and partial_failure_count > 0

    @staticmethod
    def _workbench_focus(mutable_state: dict[str, object]) -> dict[str, object]:
        workbench_runtime = mutable_state.get("workbench_runtime")
        state = workbench_runtime.get("state") if isinstance(workbench_runtime, dict) else None
        focus = state.get("active_recall_focus") if isinstance(state, dict) else None
        return dict(focus) if isinstance(focus, dict) else {}


def _string(raw: object) -> str | None:
    return raw if isinstance(raw, str) else None
