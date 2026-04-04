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
class ContinuationContract:
    continuation_token: str
    protocol_kind: str
    task_id: str
    task_name: str
    tool_name: str
    originating_turn_id: str | None
    originating_delta_id: str | None
    originating_trace_id: str | None
    resume_payload_schema: dict[str, object]
    protocol_payload: dict[str, object]
    continuation_status: str
    continuation_reason: str
    created_at: str
    resolved_at: str | None = None
    aborted_at: str | None = None

    def to_state(self) -> dict[str, object]:
        return {
            "continuation_token": self.continuation_token,
            "protocol_kind": self.protocol_kind,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "tool_name": self.tool_name,
            "originating_turn_id": self.originating_turn_id,
            "originating_delta_id": self.originating_delta_id,
            "originating_trace_id": self.originating_trace_id,
            "resume_payload_schema": dict(self.resume_payload_schema),
            "protocol_payload": dict(self.protocol_payload),
            "continuation_status": self.continuation_status,
            "continuation_reason": self.continuation_reason,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "aborted_at": self.aborted_at,
        }

    @classmethod
    def from_state(cls, raw: object) -> ContinuationContract | None:
        raw_dict = _dict(raw)
        continuation_token = raw_dict.get("continuation_token")
        protocol_kind = raw_dict.get("protocol_kind")
        task_id = raw_dict.get("task_id")
        task_name = raw_dict.get("task_name")
        tool_name = raw_dict.get("tool_name")
        continuation_status = raw_dict.get("continuation_status")
        continuation_reason = raw_dict.get("continuation_reason")
        created_at = raw_dict.get("created_at")
        if not isinstance(continuation_token, str):
            return None
        if not isinstance(protocol_kind, str):
            return None
        if not isinstance(task_id, str):
            return None
        if not isinstance(task_name, str):
            return None
        if not isinstance(tool_name, str):
            return None
        if not isinstance(continuation_status, str):
            return None
        if not isinstance(continuation_reason, str):
            return None
        if not isinstance(created_at, str):
            return None
        return cls(
            continuation_token=continuation_token,
            protocol_kind=protocol_kind,
            task_id=task_id,
            task_name=task_name,
            tool_name=tool_name,
            originating_turn_id=(
                str(originating_turn_id)
                if isinstance((originating_turn_id := raw_dict.get("originating_turn_id")), str)
                else None
            ),
            originating_delta_id=(
                str(originating_delta_id)
                if isinstance((originating_delta_id := raw_dict.get("originating_delta_id")), str)
                else None
            ),
            originating_trace_id=(
                str(originating_trace_id)
                if isinstance((originating_trace_id := raw_dict.get("originating_trace_id")), str)
                else None
            ),
            resume_payload_schema=_dict(raw_dict.get("resume_payload_schema")),
            protocol_payload=_dict(raw_dict.get("protocol_payload")),
            continuation_status=continuation_status,
            continuation_reason=continuation_reason,
            created_at=created_at,
            resolved_at=(
                str(resolved_at)
                if isinstance((resolved_at := raw_dict.get("resolved_at")), str)
                else None
            ),
            aborted_at=(
                str(aborted_at)
                if isinstance((aborted_at := raw_dict.get("aborted_at")), str)
                else None
            ),
        )


@dataclass(frozen=True)
class ContinuationResolution:
    continuation_token: str
    resolution_payload: dict[str, object]
    approved: bool
    user_input: str
    resolved_by: str
    validation_status: str
    normalized_resolution: dict[str, object]
    resolved_at: str

    def to_state(self) -> dict[str, object]:
        return {
            "continuation_token": self.continuation_token,
            "resolved_payload": dict(self.resolution_payload),
            "resolution_payload": dict(self.resolution_payload),
            "approved": self.approved,
            "user_input": self.user_input,
            "resolved_by": self.resolved_by,
            "validation_status": self.validation_status,
            "normalized_resolution": dict(self.normalized_resolution),
            "resolved_at": self.resolved_at,
        }


@dataclass(frozen=True)
class ContinuationLifecycleEvent:
    event_id: str
    continuation_token: str
    protocol_kind: str
    event_type: str
    created_at: str
    task_id: str
    task_name: str
    details: dict[str, object] = field(default_factory=dict)

    def to_state(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "continuation_token": self.continuation_token,
            "protocol_kind": self.protocol_kind,
            "event_type": self.event_type,
            "created_at": self.created_at,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ContinuationStoreState:
    active_token: str | None
    continuations: list[ContinuationContract] = field(default_factory=list)
    lifecycle_events: list[ContinuationLifecycleEvent] = field(default_factory=list)
    resume_contexts: list[dict[str, object]] = field(default_factory=list)

    def to_state(self) -> dict[str, object]:
        return {
            "active_token": self.active_token,
            "continuations": [item.to_state() for item in self.continuations],
            "lifecycle_events": [item.to_state() for item in self.lifecycle_events],
            "resume_contexts": [dict(item) for item in self.resume_contexts],
        }

    @classmethod
    def from_state(cls, raw: object) -> ContinuationStoreState:
        raw_dict = _dict(raw)
        continuations = [
            parsed
            for parsed in (
                ContinuationContract.from_state(item)
                for item in _dict_list(raw_dict.get("continuations"))
            )
            if parsed is not None
        ]
        lifecycle_events = [
            ContinuationLifecycleEvent(
                event_id=str(item.get("event_id") or ""),
                continuation_token=str(item.get("continuation_token") or ""),
                protocol_kind=str(item.get("protocol_kind") or ""),
                event_type=str(item.get("event_type") or ""),
                created_at=str(item.get("created_at") or ""),
                task_id=str(item.get("task_id") or ""),
                task_name=str(item.get("task_name") or ""),
                details=_dict(item.get("details")),
            )
            for item in _dict_list(raw_dict.get("lifecycle_events"))
        ]
        return cls(
            active_token=(
                str(active_token)
                if isinstance((active_token := raw_dict.get("active_token")), str)
                else None
            ),
            continuations=continuations,
            lifecycle_events=lifecycle_events,
            resume_contexts=_dict_list(raw_dict.get("resume_contexts")),
        )
