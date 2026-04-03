from __future__ import annotations

from datetime import UTC, datetime

from app.agent.executor import ExecutionResult
from app.agent.workflow import WorkflowGraphRuntime
from app.db.models import TaskNode, TaskNodeStatus


class PauseRuntimeService:
    def ensure_state(self, mutable_state: dict[str, object]) -> dict[str, object]:
        raw = mutable_state.get("pause")
        state = dict(raw) if isinstance(raw, dict) else {}
        for key in (
            "pending_interactions",
            "pending_approvals",
            "resolved_interactions",
            "resolved_approvals",
            "resume_contexts",
        ):
            value = state.get(key)
            state[key] = (
                [item for item in value if isinstance(item, dict)]
                if isinstance(value, list)
                else []
            )
        active = state.get("active")
        state["active"] = dict(active) if isinstance(active, dict) else None
        mutable_state["pause"] = state
        return state

    def active_pending(self, mutable_state: dict[str, object]) -> dict[str, object] | None:
        state = self.ensure_state(mutable_state)
        active = state.get("active")
        return dict(active) if isinstance(active, dict) else None

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
        }
        list_key = "pending_interactions" if protocol_kind == "interaction" else "pending_approvals"
        pending_list_raw = state.get(list_key)
        pending_list = (
            [item for item in pending_list_raw if isinstance(item, dict)]
            if isinstance(pending_list_raw, list)
            else []
        )
        pending_list = [item for item in pending_list if str(item.get("task_id") or "") != task.id]
        pending_list.append(pending_entry)
        state[list_key] = pending_list
        state["active"] = dict(pending_entry)
        mutable_state["pause"] = state
        return pending_entry

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
        active = state.get("active")
        if not isinstance(active, dict):
            return None
        continuation_token = active.get("continuation_token")
        if (
            isinstance(resume_token, str)
            and isinstance(continuation_token, str)
            and resume_token != continuation_token
        ):
            return None
        kind = str(active.get("kind") or "")
        if kind == "approval" and not approve:
            return None
        normalized_payload = (
            dict(resolution_payload) if isinstance(resolution_payload, dict) else {}
        )
        if (
            kind == "interaction"
            and not normalized_payload
            and not (isinstance(user_input, str) and user_input.strip())
        ):
            return None
        now = datetime.now(UTC).isoformat()
        resolution = {
            "approved": approve,
            "user_input": user_input if isinstance(user_input, str) else "",
            "resolution_payload": normalized_payload,
            "resolved_at": now,
        }
        resolved_entry: dict[str, object] = {
            **active,
            "status": "resolved",
            "resolution": resolution,
            "resolved_at": now,
        }
        pending_list_key = "pending_interactions" if kind == "interaction" else "pending_approvals"
        resolved_list_key = (
            "resolved_interactions" if kind == "interaction" else "resolved_approvals"
        )
        pending_list_raw = state.get(pending_list_key)
        pending_list = (
            [item for item in pending_list_raw if isinstance(item, dict)]
            if isinstance(pending_list_raw, list)
            else []
        )
        state[pending_list_key] = [
            item
            for item in pending_list
            if str(item.get("pending_id") or "") != str(active.get("pending_id") or "")
        ]
        resolved_list_raw = state.get(resolved_list_key)
        resolved_list = (
            [item for item in resolved_list_raw if isinstance(item, dict)]
            if isinstance(resolved_list_raw, list)
            else []
        )
        resolved_list.append(resolved_entry)
        state[resolved_list_key] = resolved_list
        resume_contexts_raw = state.get("resume_contexts")
        resume_contexts = (
            [item for item in resume_contexts_raw if isinstance(item, dict)]
            if isinstance(resume_contexts_raw, list)
            else []
        )
        resume_contexts = [
            item
            for item in resume_contexts
            if str(item.get("task_id") or "") != str(active.get("task_id") or "")
        ]
        active_resume_payload = active.get("resume_payload")
        resume_contexts.append(
            {
                "task_id": str(active.get("task_id") or ""),
                "kind": kind,
                "continuation_token": str(active.get("continuation_token") or ""),
                "resume_payload": (
                    dict(active_resume_payload) if isinstance(active_resume_payload, dict) else {}
                ),
                "resolution": resolution,
                "created_at": now,
            }
        )
        state["resume_contexts"] = resume_contexts
        state["active"] = None
        mutable_state["pause"] = state
        return resolved_entry

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
        resume_contexts = state.get("resume_contexts")
        if not isinstance(resume_contexts, list):
            return {}
        for item in resume_contexts:
            if isinstance(item, dict) and str(item.get("task_id") or "") == task_id:
                return dict(item)
        return {}

    def clear_resume_context(self, mutable_state: dict[str, object], *, task_id: str) -> None:
        state = self.ensure_state(mutable_state)
        resume_contexts_raw = state.get("resume_contexts")
        resume_contexts = (
            [item for item in resume_contexts_raw if isinstance(item, dict)]
            if isinstance(resume_contexts_raw, list)
            else []
        )
        state["resume_contexts"] = [
            item
            for item in resume_contexts
            if isinstance(item, dict) and str(item.get("task_id") or "") != task_id
        ]
        mutable_state["pause"] = state

    def continuity_snapshot(self, mutable_state: dict[str, object]) -> dict[str, object]:
        state = self.ensure_state(mutable_state)
        active = state.get("active")
        active_dict = dict(active) if isinstance(active, dict) else {}
        resolved_interactions_raw = state.get("resolved_interactions")
        resolved_approvals_raw = state.get("resolved_approvals")
        resolved_interactions = (
            [item for item in resolved_interactions_raw if isinstance(item, dict)]
            if isinstance(resolved_interactions_raw, list)
            else []
        )
        resolved_approvals = (
            [item for item in resolved_approvals_raw if isinstance(item, dict)]
            if isinstance(resolved_approvals_raw, list)
            else []
        )
        pending_interactions_raw = state.get("pending_interactions")
        pending_approvals_raw = state.get("pending_approvals")
        pending_interactions = (
            [item for item in pending_interactions_raw if isinstance(item, dict)]
            if isinstance(pending_interactions_raw, list)
            else []
        )
        pending_approvals = (
            [item for item in pending_approvals_raw if isinstance(item, dict)]
            if isinstance(pending_approvals_raw, list)
            else []
        )
        latest_resolved = None
        if resolved_interactions:
            latest_resolved = resolved_interactions[-1]
        elif resolved_approvals:
            latest_resolved = resolved_approvals[-1]
        return {
            "active": active_dict,
            "pending_interaction_count": len(pending_interactions),
            "pending_approval_count": len(pending_approvals),
            "latest_resolved": dict(latest_resolved) if isinstance(latest_resolved, dict) else {},
        }
