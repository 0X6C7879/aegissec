from __future__ import annotations

from dataclasses import dataclass, field


def _string(raw: object) -> str | None:
    return raw if isinstance(raw, str) else None


def _dict(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items()}


def _string_list(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str)]


def _dict_list(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


@dataclass(frozen=True)
class AssistantTurnInput:
    turn_id: str
    cycle_id: str
    current_goal: str
    stage: str | None
    active_tasks: list[str] = field(default_factory=list)
    retrieval_context: dict[str, object] = field(default_factory=dict)
    memory_context: dict[str, object] = field(default_factory=dict)
    transcript_context: dict[str, object] = field(default_factory=dict)
    reasoning_frame: dict[str, object] = field(default_factory=dict)
    workspace_context: dict[str, object] = field(default_factory=dict)
    pending_protocol_context: dict[str, object] = field(default_factory=dict)
    unresolved_questions_seed: list[str] = field(default_factory=list)
    recall_focus: dict[str, object] = field(default_factory=dict)
    prior_turn_outcome_summary: str = ""

    def to_state(self) -> dict[str, object]:
        return {
            "turn_id": self.turn_id,
            "cycle_id": self.cycle_id,
            "current_goal": self.current_goal,
            "stage": self.stage,
            "active_tasks": list(self.active_tasks),
            "retrieval_context": dict(self.retrieval_context),
            "memory_context": dict(self.memory_context),
            "transcript_context": dict(self.transcript_context),
            "reasoning_frame": dict(self.reasoning_frame),
            "workspace_context": dict(self.workspace_context),
            "pending_protocol_context": dict(self.pending_protocol_context),
            "unresolved_questions_seed": list(self.unresolved_questions_seed),
            "recall_focus": dict(self.recall_focus),
            "prior_turn_outcome_summary": self.prior_turn_outcome_summary,
        }

    @classmethod
    def from_state(cls, raw: object) -> AssistantTurnInput | None:
        raw_dict = _dict(raw)
        turn_id = _string(raw_dict.get("turn_id"))
        cycle_id = _string(raw_dict.get("cycle_id"))
        current_goal = _string(raw_dict.get("current_goal"))
        if turn_id is None or cycle_id is None or current_goal is None:
            return None
        return cls(
            turn_id=turn_id,
            cycle_id=cycle_id,
            current_goal=current_goal,
            stage=_string(raw_dict.get("stage")),
            active_tasks=_string_list(raw_dict.get("active_tasks")),
            retrieval_context=_dict(raw_dict.get("retrieval_context")),
            memory_context=_dict(raw_dict.get("memory_context")),
            transcript_context=_dict(raw_dict.get("transcript_context")),
            reasoning_frame=_dict(raw_dict.get("reasoning_frame")),
            workspace_context=_dict(raw_dict.get("workspace_context")),
            pending_protocol_context=_dict(raw_dict.get("pending_protocol_context")),
            unresolved_questions_seed=_string_list(raw_dict.get("unresolved_questions_seed")),
            recall_focus=_dict(raw_dict.get("recall_focus")),
            prior_turn_outcome_summary=str(raw_dict.get("prior_turn_outcome_summary") or ""),
        )


@dataclass(frozen=True)
class AssistantTurnPlan:
    turn_id: str
    cycle_id: str
    recommended_tool_wave: dict[str, object] = field(default_factory=dict)
    candidate_waves: list[dict[str, object]] = field(default_factory=list)
    chosen_wave: dict[str, object] = field(default_factory=dict)
    wave_decision: dict[str, object] = field(default_factory=dict)
    scheduler_mode: str | None = None
    selected_task_ids: list[str] = field(default_factory=list)
    selected_task_names: list[str] = field(default_factory=list)
    wave_priority: str = "advance"
    rationale: str = ""

    def to_state(self) -> dict[str, object]:
        return {
            "turn_id": self.turn_id,
            "cycle_id": self.cycle_id,
            "recommended_tool_wave": dict(self.recommended_tool_wave),
            "candidate_waves": [dict(item) for item in self.candidate_waves],
            "chosen_wave": dict(self.chosen_wave),
            "wave_decision": dict(self.wave_decision),
            "scheduler_mode": self.scheduler_mode,
            "selected_task_ids": list(self.selected_task_ids),
            "selected_task_names": list(self.selected_task_names),
            "wave_priority": self.wave_priority,
            "rationale": self.rationale,
        }

    @classmethod
    def from_state(cls, raw: object) -> AssistantTurnPlan | None:
        raw_dict = _dict(raw)
        turn_id = _string(raw_dict.get("turn_id"))
        cycle_id = _string(raw_dict.get("cycle_id"))
        if turn_id is None or cycle_id is None:
            return None
        return cls(
            turn_id=turn_id,
            cycle_id=cycle_id,
            recommended_tool_wave=_dict(raw_dict.get("recommended_tool_wave")),
            candidate_waves=_dict_list(raw_dict.get("candidate_waves")),
            chosen_wave=_dict(raw_dict.get("chosen_wave")),
            wave_decision=_dict(raw_dict.get("wave_decision")),
            scheduler_mode=_string(raw_dict.get("scheduler_mode")),
            selected_task_ids=_string_list(raw_dict.get("selected_task_ids")),
            selected_task_names=_string_list(raw_dict.get("selected_task_names")),
            wave_priority=str(raw_dict.get("wave_priority") or "advance"),
            rationale=str(raw_dict.get("rationale") or ""),
        )


@dataclass(frozen=True)
class AssistantTurnOutcome:
    turn_id: str
    cycle_id: str
    resulting_directive: str
    next_turn_hint: str
    unresolved_questions: list[str] = field(default_factory=list)
    carry_forward_context: str = ""
    next_action: str = "idle"
    turn_focus: dict[str, object] = field(default_factory=dict)
    resume_strategy: dict[str, object] = field(default_factory=dict)
    recall_focus: dict[str, object] = field(default_factory=dict)
    executed_task_ids: list[str] = field(default_factory=list)
    tool_result_count: int = 0
    partial_failure_count: int = 0
    reflection_summary: str = ""
    assimilation_result: dict[str, object] = field(default_factory=dict)

    def to_state(self) -> dict[str, object]:
        return {
            "turn_id": self.turn_id,
            "cycle_id": self.cycle_id,
            "resulting_directive": self.resulting_directive,
            "next_turn_hint": self.next_turn_hint,
            "unresolved_questions": list(self.unresolved_questions),
            "carry_forward_context": self.carry_forward_context,
            "next_action": self.next_action,
            "turn_focus": dict(self.turn_focus),
            "resume_strategy": dict(self.resume_strategy),
            "recall_focus": dict(self.recall_focus),
            "executed_task_ids": list(self.executed_task_ids),
            "tool_result_count": self.tool_result_count,
            "partial_failure_count": self.partial_failure_count,
            "reflection_summary": self.reflection_summary,
            "assimilation_result": dict(self.assimilation_result),
        }

    @classmethod
    def from_state(cls, raw: object) -> AssistantTurnOutcome | None:
        raw_dict = _dict(raw)
        turn_id = _string(raw_dict.get("turn_id"))
        cycle_id = _string(raw_dict.get("cycle_id"))
        resulting_directive = _string(raw_dict.get("resulting_directive"))
        next_turn_hint = _string(raw_dict.get("next_turn_hint"))
        if (
            turn_id is None
            or cycle_id is None
            or resulting_directive is None
            or next_turn_hint is None
        ):
            return None
        tool_result_count = raw_dict.get("tool_result_count")
        partial_failure_count = raw_dict.get("partial_failure_count")
        return cls(
            turn_id=turn_id,
            cycle_id=cycle_id,
            resulting_directive=resulting_directive,
            next_turn_hint=next_turn_hint,
            unresolved_questions=_string_list(raw_dict.get("unresolved_questions")),
            carry_forward_context=str(raw_dict.get("carry_forward_context") or ""),
            next_action=str(raw_dict.get("next_action") or "idle"),
            turn_focus=_dict(raw_dict.get("turn_focus")),
            resume_strategy=_dict(raw_dict.get("resume_strategy")),
            recall_focus=_dict(raw_dict.get("recall_focus")),
            executed_task_ids=_string_list(raw_dict.get("executed_task_ids")),
            tool_result_count=tool_result_count if isinstance(tool_result_count, int) else 0,
            partial_failure_count=(
                partial_failure_count if isinstance(partial_failure_count, int) else 0
            ),
            reflection_summary=str(raw_dict.get("reflection_summary") or ""),
            assimilation_result=_dict(raw_dict.get("assimilation_result")),
        )


@dataclass(frozen=True)
class AssistantTurnState:
    input: AssistantTurnInput
    plan: AssistantTurnPlan
    outcome: AssistantTurnOutcome
    history: list[dict[str, object]] = field(default_factory=list)

    def to_state(self) -> dict[str, object]:
        return {
            "input": self.input.to_state(),
            "plan": self.plan.to_state(),
            "outcome": self.outcome.to_state(),
            "history": [dict(item) for item in self.history],
        }

    @classmethod
    def from_state(cls, raw: object) -> AssistantTurnState | None:
        raw_dict = _dict(raw)
        turn_input = AssistantTurnInput.from_state(raw_dict.get("input"))
        turn_plan = AssistantTurnPlan.from_state(raw_dict.get("plan"))
        turn_outcome = AssistantTurnOutcome.from_state(raw_dict.get("outcome"))
        if turn_input is None or turn_plan is None or turn_outcome is None:
            return None
        history = _dict_list(raw_dict.get("history"))
        return cls(input=turn_input, plan=turn_plan, outcome=turn_outcome, history=history)
