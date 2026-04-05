from __future__ import annotations

from app.agent.continuation import ContinuationContract
from app.agent.continuation_store import ContinuationStore
from app.agent.executor import ExecutionResult
from app.agent.workflow import WorkflowGraphRuntime
from app.db.models import TaskNode, TaskNodeStatus


class PauseRuntimeService:
    def __init__(self) -> None:
        self._continuation_store = ContinuationStore()

    def ensure_state(self, mutable_state: dict[str, object]) -> dict[str, object]:
        raw = mutable_state.get("pause")
        state = dict(raw) if isinstance(raw, dict) else {}
        state = self._continuation_store.ensure_pause_state(state)
        mutable_state["pause"] = state
        return state

    def active_pending(self, mutable_state: dict[str, object]) -> dict[str, object] | None:
        state = self.ensure_state(mutable_state)
        return self._continuation_store.active_pending(state)

    def active_contract(self, mutable_state: dict[str, object]) -> ContinuationContract | None:
        state = self.ensure_state(mutable_state)
        return self._continuation_store.active_contract(state)

    def register_pending_protocol(
        self,
        *,
        mutable_state: dict[str, object],
        task: TaskNode,
        execution: ExecutionResult,
    ) -> dict[str, object] | None:
        state = self.ensure_state(mutable_state)
        protocol_payload = execution.output_payload.get("protocol_payload")
        if not isinstance(protocol_payload, dict):
            return None
        protocol_kind = str(protocol_payload.get("protocol_kind") or "")
        pending_payload = protocol_payload.get(protocol_kind)
        if protocol_kind not in {"interaction", "approval"}:
            return None
        deferred = protocol_payload.get("deferred_continuation")
        resume_payload = deferred.get("resume_payload") if isinstance(deferred, dict) else None
        continuation_token = (
            deferred.get("continuation_token") if isinstance(deferred, dict) else None
        )
        if not isinstance(pending_payload, dict) or not isinstance(resume_payload, dict):
            return None
        if not isinstance(continuation_token, str):
            return None
        pending_id = str(
            pending_payload.get("interaction_id")
            or pending_payload.get("approval_id")
            or f"pause-{task.id}"
        )
        formal_fields = self._formal_pending_fields(
            protocol_kind=protocol_kind,
            pending_payload=pending_payload,
        )
        expected_fields = formal_fields.get("expected_fields")
        contract = ContinuationContract(
            continuation_token=continuation_token,
            protocol_kind=protocol_kind,
            task_id=task.id,
            task_name=task.name,
            tool_name=str(execution.tool_name or "workflow.tool"),
            originating_turn_id=None,
            originating_delta_id=None,
            originating_trace_id=execution.trace_id,
            resume_payload_schema={
                "required_fields": (
                    [item for item in expected_fields if isinstance(item, str)]
                    if isinstance(expected_fields, list)
                    else ["approved"] if protocol_kind == "approval" else ["user_input"]
                ),
                "kind": protocol_kind,
            },
            protocol_payload=dict(protocol_payload),
            continuation_status="pending",
            continuation_reason=str(execution.output_payload.get("pause_reason") or ""),
            created_at=execution.ended_at.isoformat(),
        )
        pending_entry: dict[str, object] = {
            "pending_id": pending_id,
            "kind": protocol_kind,
            "task_id": task.id,
            "task_name": task.name,
            "tool_name": str(execution.tool_name or "workflow.tool"),
            "pause_reason": str(execution.output_payload.get("pause_reason") or ""),
            "resume_condition": str(execution.output_payload.get("resume_condition") or ""),
            "continuation_token": continuation_token,
            "resume_payload": dict(resume_payload),
            "protocol_payload": dict(protocol_payload),
            "created_at": execution.ended_at.isoformat(),
            "status": "pending",
            "formal_fields": formal_fields,
            "originating_trace_id": execution.trace_id,
            "resume_payload_schema": contract.resume_payload_schema,
        }
        persisted = self._continuation_store.register_pending(state, pending_entry)
        mutable_state["pause"] = state
        return persisted

    def resolve_pending(
        self,
        *,
        mutable_state: dict[str, object],
        approve: bool,
        user_input: str | None,
        resume_token: str | None,
        resolution_payload: dict[str, object] | None,
    ) -> dict[str, object] | None:
        state = self.ensure_state(mutable_state)
        resolved_entry = (
            self._continuation_store.resolve_by_token(
                state,
                continuation_token=resume_token,
                approve=approve,
                user_input=user_input,
                resolution_payload=resolution_payload,
            )
            if isinstance(resume_token, str) and resume_token
            else self._continuation_store.resolve_active(
                state,
                approve=approve,
                user_input=user_input,
                resume_token=resume_token,
                resolution_payload=resolution_payload,
            )
        )
        mutable_state["pause"] = state
        return resolved_entry

    @staticmethod
    def _formal_pending_fields(
        *, protocol_kind: str, pending_payload: dict[str, object]
    ) -> dict[str, object]:
        if protocol_kind == "approval":
            return {
                "approval_reason": str(pending_payload.get("approval_reason") or ""),
                "requested_scope": str(pending_payload.get("requested_scope") or ""),
                "risk_summary": str(pending_payload.get("risk_summary") or ""),
                "resume_hint": str(pending_payload.get("resume_hint") or ""),
            }
        return {
            "question": str(pending_payload.get("question") or ""),
            "expected_fields": (
                [item for item in fields if isinstance(item, str)]
                if isinstance((fields := pending_payload.get("expected_fields")), list)
                else []
            ),
            "context_note": str(pending_payload.get("context_note") or ""),
            "resume_hint": str(pending_payload.get("resume_hint") or ""),
        }

    def mark_task_ready_for_resolution(
        self,
        *,
        tasks: list[TaskNode],
        resolved_entry: dict[str, object],
    ) -> TaskNode | None:
        target_task_id = resolved_entry.get("task_id")
        if not isinstance(target_task_id, str):
            return None
        for task in tasks:
            if task.id != target_task_id:
                continue
            resolved_payload = resolved_entry.get("resolution")
            task.status = TaskNodeStatus.READY
            task.metadata_json = {
                **dict(task.metadata_json),
                "last_resume_resolution": (
                    dict(resolved_payload) if isinstance(resolved_payload, dict) else {}
                ),
            }
            WorkflowGraphRuntime.sync_execution_state(task)
            return task
        return None

    def resume_context_for_task(
        self, mutable_state: dict[str, object], *, task_id: str
    ) -> dict[str, object]:
        state = self.ensure_state(mutable_state)
        return self._continuation_store.resume_context_for_task(state, task_id=task_id)

    def clear_resume_context(self, mutable_state: dict[str, object], *, task_id: str) -> None:
        state = self.ensure_state(mutable_state)
        self._continuation_store.clear_resume_context(state, task_id=task_id)
        mutable_state["pause"] = state

    def continuity_snapshot(self, mutable_state: dict[str, object]) -> dict[str, object]:
        state = self.ensure_state(mutable_state)
        return self._continuation_store.continuity_snapshot(state)

    def lifecycle_events(
        self, mutable_state: dict[str, object], *, limit: int = 8
    ) -> list[dict[str, object]]:
        snapshot = self.continuity_snapshot(mutable_state)
        events = snapshot.get("lifecycle_events")
        if not isinstance(events, list):
            return []
        normalized = [item for item in events if isinstance(item, dict)]
        return normalized[-limit:]
