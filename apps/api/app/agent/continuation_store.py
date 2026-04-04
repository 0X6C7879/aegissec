from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.agent.continuation import (
    ContinuationContract,
    ContinuationLifecycleEvent,
    ContinuationResolution,
    ContinuationStoreState,
)


class ContinuationStore:
    def ensure_pause_state(self, pause_state: dict[str, object]) -> dict[str, object]:
        raw = pause_state.get("continuation")
        state = ContinuationStoreState.from_state(raw)
        if not state.continuations:
            state = self._bootstrap_from_legacy(state=state, pause_state=pause_state)
        pause_state["continuation"] = state.to_state()
        self._project_compatibility(pause_state, state)
        return pause_state

    def register_continuation(
        self, pause_state: dict[str, object], contract: ContinuationContract
    ) -> ContinuationContract:
        self.ensure_pause_state(pause_state)
        state = ContinuationStoreState.from_state(pause_state.get("continuation"))
        continuations = [
            item
            for item in state.continuations
            if item.continuation_token != contract.continuation_token
            and not (item.task_id == contract.task_id and item.continuation_status == "pending")
        ]
        normalized = ContinuationContract(
            continuation_token=contract.continuation_token,
            protocol_kind=contract.protocol_kind,
            task_id=contract.task_id,
            task_name=contract.task_name,
            tool_name=contract.tool_name,
            originating_turn_id=contract.originating_turn_id,
            originating_delta_id=contract.originating_delta_id,
            originating_trace_id=contract.originating_trace_id,
            resume_payload_schema=dict(contract.resume_payload_schema),
            protocol_payload=dict(contract.protocol_payload),
            continuation_status="pending",
            continuation_reason=contract.continuation_reason,
            created_at=contract.created_at,
            resolved_at=None,
            aborted_at=None,
        )
        continuations.append(normalized)
        lifecycle_events = list(state.lifecycle_events)
        lifecycle_events.append(
            self._event(contract=normalized, event_type="created", details={"status": "pending"})
        )
        lifecycle_events.append(self._event(contract=normalized, event_type="pending", details={}))
        next_state = ContinuationStoreState(
            active_token=normalized.continuation_token,
            continuations=continuations,
            lifecycle_events=lifecycle_events,
            resume_contexts=list(state.resume_contexts),
        )
        pause_state["continuation"] = next_state.to_state()
        self._project_compatibility(pause_state, next_state)
        return normalized

    def validate_resolution_payload(
        self,
        *,
        contract: ContinuationContract,
        approve: bool,
        user_input: str | None,
        resolution_payload: dict[str, object] | None,
    ) -> tuple[bool, dict[str, object], str]:
        payload = dict(resolution_payload) if isinstance(resolution_payload, dict) else {}
        schema = dict(contract.resume_payload_schema)
        required_fields = schema.get("required_fields")
        required = (
            [item for item in required_fields if isinstance(item, str)]
            if isinstance(required_fields, list)
            else []
        )
        if contract.protocol_kind == "approval" and not approve:
            return False, payload, "approval_required"
        if isinstance(user_input, str) and user_input.strip() and "user_input" not in payload:
            payload["user_input"] = user_input
        if contract.protocol_kind == "approval" and "approved" not in payload:
            payload["approved"] = approve
        missing = [field for field in required if field not in payload]
        if missing:
            return False, payload, f"missing_required_fields:{','.join(missing)}"
        if contract.protocol_kind == "interaction" and not payload and not user_input:
            return False, payload, "interaction_payload_required"
        return True, payload, "ok"

    def resolve_continuation(
        self,
        pause_state: dict[str, object],
        *,
        continuation_token: str,
        approve: bool,
        user_input: str | None,
        resolution_payload: dict[str, object] | None,
    ) -> tuple[ContinuationContract | None, ContinuationResolution | None, str | None]:
        self.ensure_pause_state(pause_state)
        state = ContinuationStoreState.from_state(pause_state.get("continuation"))
        contract = next(
            (
                item
                for item in state.continuations
                if item.continuation_token == continuation_token
                and item.continuation_status == "pending"
            ),
            None,
        )
        if contract is None:
            return None, None, "not_found"
        valid, normalized_payload, reason = self.validate_resolution_payload(
            contract=contract,
            approve=approve,
            user_input=user_input,
            resolution_payload=resolution_payload,
        )
        lifecycle_events = list(state.lifecycle_events)
        if not valid:
            lifecycle_events.append(
                self._event(
                    contract=contract,
                    event_type="validation_failed",
                    details={"reason": reason, "payload": normalized_payload},
                )
            )
            next_state = ContinuationStoreState(
                active_token=state.active_token,
                continuations=list(state.continuations),
                lifecycle_events=lifecycle_events,
                resume_contexts=list(state.resume_contexts),
            )
            pause_state["continuation"] = next_state.to_state()
            self._project_compatibility(pause_state, next_state)
            return contract, None, reason

        now = datetime.now(UTC).isoformat()
        normalized_resolution = {
            "approved": approve,
            "user_input": user_input if isinstance(user_input, str) else "",
            "resolution_payload": dict(normalized_payload),
        }
        resolution = ContinuationResolution(
            continuation_token=continuation_token,
            resolution_payload=normalized_payload,
            approved=approve,
            user_input=user_input if isinstance(user_input, str) else "",
            resolved_by=str(
                normalized_payload.get("resolved_by")
                or normalized_payload.get("operator")
                or normalized_payload.get("provided_by")
                or "operator"
            ),
            validation_status="validated",
            normalized_resolution=normalized_resolution,
            resolved_at=now,
        )
        resolved_contract = ContinuationContract(
            continuation_token=contract.continuation_token,
            protocol_kind=contract.protocol_kind,
            task_id=contract.task_id,
            task_name=contract.task_name,
            tool_name=contract.tool_name,
            originating_turn_id=contract.originating_turn_id,
            originating_delta_id=contract.originating_delta_id,
            originating_trace_id=contract.originating_trace_id,
            resume_payload_schema=dict(contract.resume_payload_schema),
            protocol_payload=dict(contract.protocol_payload),
            continuation_status="resolved",
            continuation_reason=contract.continuation_reason,
            created_at=contract.created_at,
            resolved_at=now,
            aborted_at=None,
        )
        continuations = [
            resolved_contract if item.continuation_token == continuation_token else item
            for item in state.continuations
        ]
        lifecycle_events.append(
            self._event(
                contract=resolved_contract,
                event_type="resolved",
                details={"resolution": resolution.to_state()},
            )
        )
        resume_contexts = [
            item
            for item in state.resume_contexts
            if str(item.get("task_id") or "") != contract.task_id
        ]
        resume_contexts.append(
            {
                "task_id": contract.task_id,
                "kind": contract.protocol_kind,
                "continuation_token": contract.continuation_token,
                "resume_payload": self._dict(
                    self._dict(contract.protocol_payload.get("deferred_continuation")).get(
                        "resume_payload"
                    )
                ),
                "resolution": resolution.to_state(),
                "created_at": now,
            }
        )
        next_state = ContinuationStoreState(
            active_token=(None if state.active_token == continuation_token else state.active_token),
            continuations=continuations,
            lifecycle_events=lifecycle_events,
            resume_contexts=resume_contexts,
        )
        pause_state["continuation"] = next_state.to_state()
        self._project_compatibility(pause_state, next_state)
        return resolved_contract, resolution, None

    def abort_continuation(
        self,
        pause_state: dict[str, object],
        *,
        continuation_token: str,
        reason: str,
    ) -> ContinuationContract | None:
        self.ensure_pause_state(pause_state)
        state = ContinuationStoreState.from_state(pause_state.get("continuation"))
        target = next(
            (
                item
                for item in state.continuations
                if item.continuation_token == continuation_token
                and item.continuation_status == "pending"
            ),
            None,
        )
        if target is None:
            return None
        now = datetime.now(UTC).isoformat()
        aborted = ContinuationContract(
            continuation_token=target.continuation_token,
            protocol_kind=target.protocol_kind,
            task_id=target.task_id,
            task_name=target.task_name,
            tool_name=target.tool_name,
            originating_turn_id=target.originating_turn_id,
            originating_delta_id=target.originating_delta_id,
            originating_trace_id=target.originating_trace_id,
            resume_payload_schema=dict(target.resume_payload_schema),
            protocol_payload=dict(target.protocol_payload),
            continuation_status="aborted",
            continuation_reason=reason,
            created_at=target.created_at,
            resolved_at=target.resolved_at,
            aborted_at=now,
        )
        continuations = [
            aborted if item.continuation_token == continuation_token else item
            for item in state.continuations
        ]
        lifecycle_events = list(state.lifecycle_events)
        lifecycle_events.append(
            self._event(contract=aborted, event_type="aborted", details={"reason": reason})
        )
        next_state = ContinuationStoreState(
            active_token=(None if state.active_token == continuation_token else state.active_token),
            continuations=continuations,
            lifecycle_events=lifecycle_events,
            resume_contexts=list(state.resume_contexts),
        )
        pause_state["continuation"] = next_state.to_state()
        self._project_compatibility(pause_state, next_state)
        return aborted

    def continuation_for_task(
        self, pause_state: dict[str, object], *, task_id: str
    ) -> ContinuationContract | None:
        self.ensure_pause_state(pause_state)
        state = ContinuationStoreState.from_state(pause_state.get("continuation"))
        return next(
            (
                item
                for item in state.continuations
                if item.task_id == task_id and item.continuation_status == "pending"
            ),
            None,
        )

    def active_continuations(self, pause_state: dict[str, object]) -> list[ContinuationContract]:
        self.ensure_pause_state(pause_state)
        state = ContinuationStoreState.from_state(pause_state.get("continuation"))
        return [item for item in state.continuations if item.continuation_status == "pending"]

    def active_contract(self, pause_state: dict[str, object]) -> ContinuationContract | None:
        self.ensure_pause_state(pause_state)
        state = ContinuationStoreState.from_state(pause_state.get("continuation"))
        pending = [item for item in state.continuations if item.continuation_status == "pending"]
        if state.active_token:
            active = next(
                (item for item in pending if item.continuation_token == state.active_token),
                None,
            )
            if active is not None:
                return active
        if len(pending) == 1:
            return pending[0]
        return None

    def continuation_for_token(
        self,
        pause_state: dict[str, object],
        *,
        continuation_token: str,
    ) -> ContinuationContract | None:
        self.ensure_pause_state(pause_state)
        state = ContinuationStoreState.from_state(pause_state.get("continuation"))
        return next(
            (
                item
                for item in state.continuations
                if item.continuation_token == continuation_token
                and item.continuation_status == "pending"
            ),
            None,
        )

    def active_pending(self, pause_state: dict[str, object]) -> dict[str, object] | None:
        active = self.active_contract(pause_state)
        return self._compatibility_entry(active) if active is not None else None

    def register_pending(
        self, pause_state: dict[str, object], entry: dict[str, object]
    ) -> dict[str, object]:
        contract = ContinuationContract(
            continuation_token=str(entry.get("continuation_token") or ""),
            protocol_kind=str(entry.get("kind") or "interaction"),
            task_id=str(entry.get("task_id") or ""),
            task_name=str(entry.get("task_name") or "workflow-task"),
            tool_name=str(entry.get("tool_name") or "workflow.tool"),
            originating_turn_id=(
                str(entry.get("originating_turn_id"))
                if isinstance(entry.get("originating_turn_id"), str)
                else None
            ),
            originating_delta_id=(
                str(entry.get("originating_delta_id"))
                if isinstance(entry.get("originating_delta_id"), str)
                else None
            ),
            originating_trace_id=(
                str(entry.get("originating_trace_id"))
                if isinstance(entry.get("originating_trace_id"), str)
                else None
            ),
            resume_payload_schema=self._dict(entry.get("resume_payload_schema")),
            protocol_payload=self._dict(entry.get("protocol_payload")),
            continuation_status="pending",
            continuation_reason=str(entry.get("pause_reason") or "awaiting continuation"),
            created_at=str(entry.get("created_at") or datetime.now(UTC).isoformat()),
        )
        if not contract.continuation_token:
            contract = ContinuationContract(
                continuation_token=f"resume-{uuid4()}",
                protocol_kind=contract.protocol_kind,
                task_id=contract.task_id,
                task_name=contract.task_name,
                tool_name=contract.tool_name,
                originating_turn_id=contract.originating_turn_id,
                originating_delta_id=contract.originating_delta_id,
                originating_trace_id=contract.originating_trace_id,
                resume_payload_schema=dict(contract.resume_payload_schema),
                protocol_payload=dict(contract.protocol_payload),
                continuation_status="pending",
                continuation_reason=contract.continuation_reason,
                created_at=contract.created_at,
            )
        persisted = self.register_continuation(pause_state, contract)
        pending_id = str(entry.get("pending_id") or f"pause-{persisted.task_id}")
        return {
            **dict(entry),
            "pending_id": pending_id,
            "kind": persisted.protocol_kind,
            "continuation_token": persisted.continuation_token,
            "task_id": persisted.task_id,
            "task_name": persisted.task_name,
            "status": "pending",
            "created_at": persisted.created_at,
        }

    def resolve_active(
        self,
        pause_state: dict[str, object],
        *,
        approve: bool,
        user_input: str | None,
        resume_token: str | None,
        resolution_payload: dict[str, object] | None,
    ) -> dict[str, object] | None:
        active = self.active_contract(pause_state)
        if active is None:
            return None
        if (
            isinstance(resume_token, str)
            and resume_token
            and resume_token != active.continuation_token
        ):
            return None
        return self.resolve_by_token(
            pause_state,
            continuation_token=active.continuation_token,
            approve=approve,
            user_input=user_input,
            resolution_payload=resolution_payload,
        )

    def resolve_by_token(
        self,
        pause_state: dict[str, object],
        *,
        continuation_token: str,
        approve: bool,
        user_input: str | None,
        resolution_payload: dict[str, object] | None,
    ) -> dict[str, object] | None:
        contract = self.continuation_for_token(
            pause_state,
            continuation_token=continuation_token,
        )
        if contract is None:
            pause_state["last_resolution_error"] = {
                "continuation_token": continuation_token,
                "error": "not_found",
            }
            return None
        resolved_contract, resolution, error = self.resolve_continuation(
            pause_state,
            continuation_token=continuation_token,
            approve=approve,
            user_input=user_input,
            resolution_payload=resolution_payload,
        )
        if resolved_contract is None or resolution is None:
            if error is not None:
                pause_state["last_resolution_error"] = {
                    "continuation_token": continuation_token,
                    "error": error,
                }
            return None
        return {
            "pending_id": str(
                self._compatibility_entry(contract).get("pending_id")
                or f"pause-{resolved_contract.task_id}"
            ),
            "kind": resolved_contract.protocol_kind,
            "task_id": resolved_contract.task_id,
            "task_name": resolved_contract.task_name,
            "tool_name": resolved_contract.tool_name,
            "continuation_token": resolved_contract.continuation_token,
            "status": "resolved",
            "created_at": resolved_contract.created_at,
            "resolved_at": resolved_contract.resolved_at,
            "resolution": {
                "approved": resolution.approved,
                "user_input": resolution.user_input,
                "resolution_payload": dict(resolution.resolution_payload),
                "resolved_by": resolution.resolved_by,
                "validation_status": resolution.validation_status,
                "normalized_resolution": dict(resolution.normalized_resolution),
                "resolved_at": resolution.resolved_at,
            },
        }

    def resume_context_for_task(
        self, pause_state: dict[str, object], *, task_id: str
    ) -> dict[str, object]:
        self.ensure_pause_state(pause_state)
        state = ContinuationStoreState.from_state(pause_state.get("continuation"))
        for item in state.resume_contexts:
            if str(item.get("task_id") or "") == task_id:
                return dict(item)
        return {}

    def clear_resume_context(self, pause_state: dict[str, object], *, task_id: str) -> None:
        self.ensure_pause_state(pause_state)
        state = ContinuationStoreState.from_state(pause_state.get("continuation"))
        next_state = ContinuationStoreState(
            active_token=state.active_token,
            continuations=list(state.continuations),
            lifecycle_events=list(state.lifecycle_events),
            resume_contexts=[
                item
                for item in state.resume_contexts
                if str(item.get("task_id") or "") != task_id
                or isinstance(item.get("resolution"), dict)
            ],
        )
        pause_state["continuation"] = next_state.to_state()
        self._project_compatibility(pause_state, next_state)

    def continuity_snapshot(self, pause_state: dict[str, object]) -> dict[str, object]:
        self.ensure_pause_state(pause_state)
        state = ContinuationStoreState.from_state(pause_state.get("continuation"))
        active = self._compatibility_entry(self.active_contract(pause_state))
        resolved = [item for item in state.continuations if item.continuation_status == "resolved"]
        latest_resolved = resolved[-1] if resolved else None
        resolution_contexts = [item for item in state.resume_contexts if isinstance(item, dict)]
        pending = [item for item in state.continuations if item.continuation_status == "pending"]
        pending_interactions = [item for item in pending if item.protocol_kind == "interaction"]
        pending_approvals = [item for item in pending if item.protocol_kind == "approval"]
        return {
            "active": active,
            "pending_interaction_count": len(pending_interactions),
            "pending_approval_count": len(pending_approvals),
            "latest_resolved": (
                latest_resolved.to_state()
                | {
                    "resolution": self._resolution_for_contract(
                        latest_resolved,
                        resolution_contexts=resolution_contexts,
                    )
                }
                if latest_resolved is not None
                else {}
            ),
            "active_continuations": [item.to_state() for item in pending],
            "lifecycle_events": [item.to_state() for item in state.lifecycle_events[-12:]],
        }

    def _project_compatibility(
        self, pause_state: dict[str, object], state: ContinuationStoreState
    ) -> None:
        pending = [item for item in state.continuations if item.continuation_status == "pending"]
        resolved = [item for item in state.continuations if item.continuation_status == "resolved"]
        resolution_contexts = [item for item in state.resume_contexts if isinstance(item, dict)]
        pause_state["pending_interactions"] = [
            self._compatibility_entry(item)
            for item in pending
            if item.protocol_kind == "interaction"
        ]
        pause_state["pending_approvals"] = [
            self._compatibility_entry(item) for item in pending if item.protocol_kind == "approval"
        ]
        resolved_interactions: list[dict[str, object]] = []
        resolved_approvals: list[dict[str, object]] = []
        for item in resolved:
            projected = self._compatibility_entry(item)
            projected["resolution"] = self._resolution_for_contract(
                item,
                resolution_contexts=resolution_contexts,
            )
            if item.protocol_kind == "interaction":
                resolved_interactions.append(projected)
            if item.protocol_kind == "approval":
                resolved_approvals.append(projected)
        pause_state["resolved_interactions"] = resolved_interactions
        pause_state["resolved_approvals"] = resolved_approvals
        pause_state["resume_contexts"] = [dict(item) for item in state.resume_contexts]
        active_token = state.active_token
        active = next(
            (item for item in pending if item.continuation_token == active_token),
            None,
        )
        pause_state["active"] = self._compatibility_entry(active) if active is not None else None

    def _bootstrap_from_legacy(
        self,
        *,
        state: ContinuationStoreState,
        pause_state: dict[str, object],
    ) -> ContinuationStoreState:
        pending_entries = []
        for key in ("pending_interactions", "pending_approvals"):
            value = pause_state.get(key)
            if isinstance(value, list):
                pending_entries.extend([item for item in value if isinstance(item, dict)])
        continuations = list(state.continuations)
        lifecycle_events = list(state.lifecycle_events)
        for entry in pending_entries:
            contract = ContinuationContract(
                continuation_token=str(entry.get("continuation_token") or f"resume-{uuid4()}"),
                protocol_kind=str(entry.get("kind") or "interaction"),
                task_id=str(entry.get("task_id") or ""),
                task_name=str(entry.get("task_name") or "workflow-task"),
                tool_name=str(entry.get("tool_name") or "workflow.tool"),
                originating_turn_id=None,
                originating_delta_id=None,
                originating_trace_id=None,
                resume_payload_schema={},
                protocol_payload=self._dict(entry.get("protocol_payload")),
                continuation_status="pending",
                continuation_reason=str(entry.get("pause_reason") or "awaiting continuation"),
                created_at=str(entry.get("created_at") or datetime.now(UTC).isoformat()),
            )
            continuations.append(contract)
            lifecycle_events.append(
                self._event(contract=contract, event_type="created", details={})
            )
        active = pause_state.get("active")
        active_token = (
            str(active.get("continuation_token") or "") if isinstance(active, dict) else ""
        )
        return ContinuationStoreState(
            active_token=active_token or state.active_token,
            continuations=continuations,
            lifecycle_events=lifecycle_events,
            resume_contexts=list(state.resume_contexts),
        )

    @staticmethod
    def _compatibility_entry(contract: ContinuationContract | None) -> dict[str, object]:
        if contract is None:
            return {}
        protocol_kind = contract.protocol_kind
        protocol_payload = dict(contract.protocol_payload)
        details = protocol_payload.get(protocol_kind)
        details_dict = ContinuationStore._dict(details)
        deferred = ContinuationStore._dict(protocol_payload.get("deferred_continuation"))
        resume_payload = ContinuationStore._dict(deferred.get("resume_payload"))
        pending_id = str(
            details_dict.get("interaction_id")
            or details_dict.get("approval_id")
            or f"pause-{contract.task_id}"
        )
        return {
            "pending_id": pending_id,
            "kind": contract.protocol_kind,
            "task_id": contract.task_id,
            "task_name": contract.task_name,
            "tool_name": contract.tool_name,
            "pause_reason": contract.continuation_reason,
            "resume_condition": "resume with continuation token",
            "continuation_token": contract.continuation_token,
            "resume_payload": resume_payload,
            "protocol_payload": protocol_payload,
            "created_at": contract.created_at,
            "status": contract.continuation_status,
            "resolved_at": contract.resolved_at,
            "aborted_at": contract.aborted_at,
        }

    @staticmethod
    def _event(
        *,
        contract: ContinuationContract,
        event_type: str,
        details: dict[str, object],
    ) -> ContinuationLifecycleEvent:
        return ContinuationLifecycleEvent(
            event_id=f"continuation-event-{uuid4()}",
            continuation_token=contract.continuation_token,
            protocol_kind=contract.protocol_kind,
            event_type=event_type,
            created_at=datetime.now(UTC).isoformat(),
            task_id=contract.task_id,
            task_name=contract.task_name,
            details=dict(details),
        )

    @staticmethod
    def _dict(raw: object) -> dict[str, object]:
        if not isinstance(raw, dict):
            return {}
        return {str(key): value for key, value in raw.items()}

    def _resolution_for_contract(
        self,
        contract: ContinuationContract,
        *,
        resolution_contexts: list[dict[str, object]],
    ) -> dict[str, object]:
        for item in resolution_contexts:
            token = str(item.get("continuation_token") or "")
            task_id = str(item.get("task_id") or "")
            if token == contract.continuation_token or task_id == contract.task_id:
                return self._dict(item.get("resolution"))
        return {}
