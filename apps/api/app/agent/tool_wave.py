from __future__ import annotations

from dataclasses import dataclass, field


def _dict(raw: object) -> dict[str, object]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items()}


def _dict_list(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


@dataclass(frozen=True)
class ToolWaveCandidate:
    wave_id: str
    wave_type: str
    scheduler_mode: str | None
    task_ids: list[str] = field(default_factory=list)
    task_names: list[str] = field(default_factory=list)
    rationale: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def to_state(self) -> dict[str, object]:
        return {
            "wave_id": self.wave_id,
            "wave_type": self.wave_type,
            "scheduler_mode": self.scheduler_mode,
            "task_ids": list(self.task_ids),
            "task_names": list(self.task_names),
            "rationale": self.rationale,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_state(cls, raw: object) -> ToolWaveCandidate | None:
        raw_dict = _dict(raw)
        wave_id = raw_dict.get("wave_id")
        wave_type = raw_dict.get("wave_type")
        if not isinstance(wave_id, str) or not isinstance(wave_type, str):
            return None
        task_ids_raw = raw_dict.get("task_ids")
        task_names_raw = raw_dict.get("task_names")
        scheduler_mode = raw_dict.get("scheduler_mode")
        return cls(
            wave_id=wave_id,
            wave_type=wave_type,
            scheduler_mode=scheduler_mode if isinstance(scheduler_mode, str) else None,
            task_ids=(
                [item for item in task_ids_raw if isinstance(item, str)]
                if isinstance(task_ids_raw, list)
                else []
            ),
            task_names=(
                [item for item in task_names_raw if isinstance(item, str)]
                if isinstance(task_names_raw, list)
                else []
            ),
            rationale=str(raw_dict.get("rationale") or ""),
            metadata=_dict(raw_dict.get("metadata")),
        )


@dataclass(frozen=True)
class ToolWaveDecision:
    decision: str
    selected_wave_id: str
    reason: str
    confidence: float = 0.0
    metadata: dict[str, object] = field(default_factory=dict)

    def to_state(self) -> dict[str, object]:
        return {
            "decision": self.decision,
            "selected_wave_id": self.selected_wave_id,
            "reason": self.reason,
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_state(cls, raw: object) -> ToolWaveDecision | None:
        raw_dict = _dict(raw)
        decision = raw_dict.get("decision")
        selected_wave_id = raw_dict.get("selected_wave_id")
        reason = raw_dict.get("reason")
        if not isinstance(decision, str):
            return None
        if not isinstance(selected_wave_id, str):
            return None
        if not isinstance(reason, str):
            return None
        confidence_raw = raw_dict.get("confidence")
        confidence = float(confidence_raw) if isinstance(confidence_raw, int | float) else 0.0
        return cls(
            decision=decision,
            selected_wave_id=selected_wave_id,
            reason=reason,
            confidence=confidence,
            metadata=_dict(raw_dict.get("metadata")),
        )


@dataclass(frozen=True)
class ToolWaveExecutionFrame:
    wave_id: str
    task_ids: list[str] = field(default_factory=list)
    task_names: list[str] = field(default_factory=list)
    scheduler_group: str | None = None
    mode: str = "execute"

    def to_state(self) -> dict[str, object]:
        return {
            "wave_id": self.wave_id,
            "task_ids": list(self.task_ids),
            "task_names": list(self.task_names),
            "scheduler_group": self.scheduler_group,
            "mode": self.mode,
        }

    @classmethod
    def from_state(cls, raw: object) -> ToolWaveExecutionFrame | None:
        raw_dict = _dict(raw)
        wave_id = raw_dict.get("wave_id")
        if not isinstance(wave_id, str):
            return None
        task_ids_raw = raw_dict.get("task_ids")
        task_names_raw = raw_dict.get("task_names")
        scheduler_group = raw_dict.get("scheduler_group")
        mode = raw_dict.get("mode")
        return cls(
            wave_id=wave_id,
            task_ids=(
                [item for item in task_ids_raw if isinstance(item, str)]
                if isinstance(task_ids_raw, list)
                else []
            ),
            task_names=(
                [item for item in task_names_raw if isinstance(item, str)]
                if isinstance(task_names_raw, list)
                else []
            ),
            scheduler_group=scheduler_group if isinstance(scheduler_group, str) else None,
            mode=mode if isinstance(mode, str) else "execute",
        )


@dataclass(frozen=True)
class TurnAssimilationResult:
    selected_wave_id: str
    expected_task_count: int
    executed_task_count: int
    executed_task_ids: list[str] = field(default_factory=list)
    partial_failure_count: int = 0
    directive: str = "continue"
    status: str = "assimilated"
    summary: str = ""

    def to_state(self) -> dict[str, object]:
        return {
            "selected_wave_id": self.selected_wave_id,
            "expected_task_count": self.expected_task_count,
            "executed_task_count": self.executed_task_count,
            "executed_task_ids": list(self.executed_task_ids),
            "partial_failure_count": self.partial_failure_count,
            "directive": self.directive,
            "status": self.status,
            "summary": self.summary,
        }

    @classmethod
    def from_state(cls, raw: object) -> TurnAssimilationResult | None:
        raw_dict = _dict(raw)
        selected_wave_id = raw_dict.get("selected_wave_id")
        expected_task_count = raw_dict.get("expected_task_count")
        executed_task_count = raw_dict.get("executed_task_count")
        if not isinstance(selected_wave_id, str):
            return None
        if not isinstance(expected_task_count, int) or not isinstance(executed_task_count, int):
            return None
        executed_task_ids = raw_dict.get("executed_task_ids")
        partial_failure_count = raw_dict.get("partial_failure_count")
        directive = raw_dict.get("directive")
        status = raw_dict.get("status")
        summary = raw_dict.get("summary")
        return cls(
            selected_wave_id=selected_wave_id,
            expected_task_count=expected_task_count,
            executed_task_count=executed_task_count,
            executed_task_ids=(
                [item for item in executed_task_ids if isinstance(item, str)]
                if isinstance(executed_task_ids, list)
                else []
            ),
            partial_failure_count=(
                partial_failure_count if isinstance(partial_failure_count, int) else 0
            ),
            directive=directive if isinstance(directive, str) else "continue",
            status=status if isinstance(status, str) else "assimilated",
            summary=summary if isinstance(summary, str) else "",
        )


@dataclass(frozen=True)
class AssistantExecutionFrame:
    frame_id: str
    cycle_id: str
    candidate_waves: list[ToolWaveCandidate] = field(default_factory=list)
    chosen_wave: ToolWaveCandidate | None = None
    wave_decision: ToolWaveDecision | None = None

    @property
    def chosen_task_ids(self) -> list[str]:
        if self.chosen_wave is None:
            return []
        return list(self.chosen_wave.task_ids)

    def to_state(self) -> dict[str, object]:
        return {
            "frame_id": self.frame_id,
            "cycle_id": self.cycle_id,
            "candidate_waves": [item.to_state() for item in self.candidate_waves],
            "chosen_wave": self.chosen_wave.to_state() if self.chosen_wave is not None else {},
            "wave_decision": (
                self.wave_decision.to_state() if self.wave_decision is not None else {}
            ),
        }

    def candidate_waves_state(self) -> list[dict[str, object]]:
        return [item.to_state() for item in self.candidate_waves]

    def chosen_wave_state(self) -> dict[str, object]:
        return self.chosen_wave.to_state() if self.chosen_wave is not None else {}

    def wave_decision_state(self) -> dict[str, object]:
        return self.wave_decision.to_state() if self.wave_decision is not None else {}

    @classmethod
    def from_state(cls, raw: object) -> AssistantExecutionFrame | None:
        raw_dict = _dict(raw)
        frame_id = raw_dict.get("frame_id")
        cycle_id = raw_dict.get("cycle_id")
        if not isinstance(frame_id, str) or not isinstance(cycle_id, str):
            return None
        candidate_waves = [
            item
            for item in (
                ToolWaveCandidate.from_state(raw_item)
                for raw_item in _dict_list(raw_dict.get("candidate_waves"))
            )
            if item is not None
        ]
        return cls(
            frame_id=frame_id,
            cycle_id=cycle_id,
            candidate_waves=candidate_waves,
            chosen_wave=ToolWaveCandidate.from_state(raw_dict.get("chosen_wave")),
            wave_decision=ToolWaveDecision.from_state(raw_dict.get("wave_decision")),
        )
