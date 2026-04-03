from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.agent.context_models import RetrievalState
from app.agent.token_budget import estimate_token_count


@dataclass(frozen=True)
class SessionSummarySnapshot:
    summary: str
    updated_at: str | None
    should_persist: bool
    trigger_reason: str | None
    rough_token_count: int
    tokens_since_update: int
    tool_calls_since_update: int

    def to_state(self) -> dict[str, object]:
        return {
            "summary": self.summary,
            "updated_at": self.updated_at,
            "should_persist": self.should_persist,
            "trigger_reason": self.trigger_reason,
            "rough_token_count": self.rough_token_count,
            "tokens_since_update": self.tokens_since_update,
            "tool_calls_since_update": self.tool_calls_since_update,
        }


class SessionMemoryService:
    MIN_ROUGH_TOKEN_THRESHOLD = 120
    MIN_TOKENS_BETWEEN_UPDATES = 80
    MIN_TOOL_CALLS_BETWEEN_UPDATES = 2

    def update_session_summary(
        self,
        *,
        state: dict[str, object],
        retrieval: RetrievalState,
    ) -> SessionSummarySnapshot:
        memory_state = self._memory_state(state)
        previous = memory_state.get("session_summary")
        previous_summary = dict(previous) if isinstance(previous, dict) else {}
        source_text = self._build_source_text(state=state, retrieval=retrieval)
        rough_token_count = estimate_token_count(source_text)
        tool_calls = self._tool_call_count(state)
        last_tool_calls = self._int_value(previous_summary.get("tool_call_count"))
        tool_calls_since_update = max(tool_calls - last_tool_calls, 0)
        last_token_count = self._int_value(previous_summary.get("source_token_count"))
        tokens_since_update = max(rough_token_count - last_token_count, 0)
        natural_breakpoint, breakpoint_reason = self._natural_breakpoint(state, previous_summary)
        has_previous_summary = isinstance(previous_summary.get("summary"), str) and bool(
            str(previous_summary.get("summary") or "").strip()
        )
        should_persist = (
            rough_token_count >= self.MIN_ROUGH_TOKEN_THRESHOLD
            and natural_breakpoint
            and (
                not has_previous_summary
                or tokens_since_update >= self.MIN_TOKENS_BETWEEN_UPDATES
                or tool_calls_since_update >= self.MIN_TOOL_CALLS_BETWEEN_UPDATES
            )
        )
        summary = (
            self._summarize(state=state, retrieval=retrieval)
            if should_persist
            else str(previous_summary.get("summary") or "")
        )
        updated_at = (
            datetime.now(UTC).isoformat()
            if should_persist
            else self._string_value(previous_summary.get("updated_at"))
        )
        trigger_reason = breakpoint_reason if should_persist else None
        snapshot = SessionSummarySnapshot(
            summary=summary,
            updated_at=updated_at,
            should_persist=should_persist,
            trigger_reason=trigger_reason,
            rough_token_count=rough_token_count,
            tokens_since_update=tokens_since_update,
            tool_calls_since_update=tool_calls_since_update,
        )
        if should_persist:
            memory_state["session_summary"] = {
                **snapshot.to_state(),
                "tool_call_count": tool_calls,
                "source_token_count": rough_token_count,
                "batch_cycle": self._batch_cycle(state),
                "current_stage": str(state.get("current_stage") or ""),
            }
            state["memory_service"] = memory_state
        elif previous_summary:
            state["memory_service"] = memory_state
        return snapshot

    @staticmethod
    def _memory_state(state: dict[str, object]) -> dict[str, object]:
        raw = state.get("memory_service")
        return dict(raw) if isinstance(raw, dict) else {}

    @staticmethod
    def _dict_list(raw: object) -> list[dict[str, object]]:
        if not isinstance(raw, list):
            return []
        return [item for item in raw if isinstance(item, dict)]

    def _build_source_text(self, *, state: dict[str, object], retrieval: RetrievalState) -> str:
        execution_records = self._dict_list(state.get("execution_records"))[-6:]
        findings = self._dict_list(state.get("findings"))[-4:]
        messages = self._dict_list(state.get("messages"))[-4:]
        parts = [
            f"Goal: {state.get('goal') or ''}",
            f"Stage: {state.get('current_stage') or ''}",
            f"Retrieval: {retrieval.summary}",
        ]
        parts.extend(
            (
                "Execution "
                f"{record.get('command_or_action') or record.get('task_node_id')}: "
                f"{record.get('summary') or record.get('status') or ''}"
            )
            for record in execution_records
        )
        parts.extend(
            f"Finding {finding.get('title') or finding.get('id')}: {finding.get('summary') or ''}"
            for finding in findings
        )
        parts.extend(
            (
                f"Message {message.get('role') or 'unknown'}: "
                f"{message.get('content') or message.get('text') or ''}"
            )
            for message in messages
        )
        return "\n".join(part for part in parts if part.strip())

    def _summarize(self, *, state: dict[str, object], retrieval: RetrievalState) -> str:
        execution_records = self._dict_list(state.get("execution_records"))[-5:]
        findings = self._dict_list(state.get("findings"))[-3:]
        recent_steps = "; ".join(
            (
                f"{record.get('command_or_action') or record.get('task_node_id')}: "
                f"{record.get('status') or 'unknown'}"
            )
            for record in execution_records
        )
        finding_text = "; ".join(
            str(finding.get("title") or finding.get("id") or "finding") for finding in findings
        )
        summary_parts = [
            f"Goal: {state.get('goal') or 'authorized assessment'}.",
            f"Current stage: {state.get('current_stage') or 'unknown'}.",
        ]
        if recent_steps:
            summary_parts.append(f"Recent tool activity: {recent_steps}.")
        if finding_text:
            summary_parts.append(f"Recent findings: {finding_text}.")
        summary_parts.append(f"Retrieval status: {retrieval.summary}.")
        return " ".join(summary_parts)

    def _natural_breakpoint(
        self, state: dict[str, object], previous_summary: dict[str, object]
    ) -> tuple[bool, str | None]:
        approval = state.get("approval")
        if isinstance(approval, dict) and approval.get("required") is True:
            return True, "approval_boundary"
        batch = state.get("batch")
        if isinstance(batch, dict):
            status = str(batch.get("status") or "")
            if status in {"completed", "waiting_approval"}:
                return True, f"batch_{status}"
            current_cycle = self._int_value(batch.get("cycle"))
            previous_cycle = self._int_value(previous_summary.get("batch_cycle"))
            if current_cycle > previous_cycle and current_cycle > 0:
                return True, "batch_cycle_advanced"
        previous_stage = self._string_value(previous_summary.get("current_stage"))
        current_stage = str(state.get("current_stage") or "")
        if previous_stage is not None and current_stage and previous_stage != current_stage:
            return True, "stage_changed"
        if previous_stage is None and current_stage:
            return True, "initial_stage"
        return False, None

    def _tool_call_count(self, state: dict[str, object]) -> int:
        return len(self._dict_list(state.get("execution_records"))) + len(
            self._dict_list(state.get("archived_execution_records"))
        )

    @staticmethod
    def _batch_cycle(state: dict[str, object]) -> int:
        batch = state.get("batch")
        if isinstance(batch, dict):
            cycle = batch.get("cycle")
            if isinstance(cycle, int):
                return cycle
        return 0

    @staticmethod
    def _int_value(raw: object) -> int:
        return raw if isinstance(raw, int) else 0

    @staticmethod
    def _string_value(raw: object) -> str | None:
        return raw if isinstance(raw, str) and raw else None
