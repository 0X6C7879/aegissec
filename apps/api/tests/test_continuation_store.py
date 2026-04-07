from __future__ import annotations

from app.agent.continuation_store import ContinuationStore
from app.agent.pause_runtime import PauseRuntimeService


def _pending_entry(*, continuation_token: str, kind: str = "interaction") -> dict[str, object]:
    return {
        "pending_id": "pause-task-1",
        "kind": kind,
        "task_id": "task-1",
        "task_name": "scope_clarification",
        "tool_name": "workflow.ask_user_question",
        "pause_reason": "await operator input",
        "resume_condition": "provide user input",
        "continuation_token": continuation_token,
        "resume_payload": {"resolution_kind": kind},
        "protocol_payload": {
            "protocol_kind": kind,
            kind: {
                "question": "Which host should the agent validate first?",
                "expected_fields": ["user_input"],
            },
            "deferred_continuation": {
                "continuation_token": continuation_token,
                "resume_payload": {"resolution_kind": kind},
            },
        },
        "created_at": "2026-04-04T00:00:00+00:00",
        "status": "pending",
        "resume_payload_schema": {
            "required_fields": ["user_input"],
            "kind": kind,
        },
    }


def test_continuation_store_active_accessors_prefer_authoritative_contract_state() -> None:
    store = ContinuationStore()
    pause_state: dict[str, object] = {
        "active": {"continuation_token": "legacy-token", "task_id": "legacy-task"}
    }

    persisted = store.register_pending(pause_state, _pending_entry(continuation_token="token-1"))
    pause_state["active"] = {"continuation_token": "stale-token", "task_id": "stale-task"}

    active_contract = store.active_contract(pause_state)
    active_pending = store.active_pending(pause_state)
    continuity = store.continuity_snapshot(pause_state)
    active_snapshot = continuity["active"]

    assert active_contract is not None
    assert active_contract.continuation_token == persisted["continuation_token"]
    assert active_pending is not None
    assert active_pending["continuation_token"] == persisted["continuation_token"]
    assert isinstance(active_snapshot, dict)
    assert active_snapshot["continuation_token"] == persisted["continuation_token"]
    compatibility_active = pause_state["active"]
    assert isinstance(compatibility_active, dict)
    assert compatibility_active["continuation_token"] == persisted["continuation_token"]


def test_pause_runtime_resolves_by_token_without_legacy_active_primary() -> None:
    runtime = PauseRuntimeService()
    store = ContinuationStore()
    mutable_state: dict[str, object] = {"pause": {}}
    pause_state = runtime.ensure_state(mutable_state)
    store.register_pending(pause_state, _pending_entry(continuation_token="token-1"))
    pause_snapshot = mutable_state["pause"]
    assert isinstance(pause_snapshot, dict)
    pause_snapshot["active"] = None

    resolved = runtime.resolve_pending(
        mutable_state=mutable_state,
        approve=False,
        user_input="Validate app.internal.example first.",
        resume_token="token-1",
        resolution_payload={"provided_by": "unit-test"},
    )

    pause_snapshot = mutable_state["pause"]
    assert isinstance(pause_snapshot, dict)
    continuation_state = pause_snapshot["continuation"]
    assert isinstance(continuation_state, dict)

    assert resolved is not None
    assert resolved["continuation_token"] == "token-1"
    assert resolved["status"] == "resolved"
    resolution = resolved["resolution"]
    assert isinstance(resolution, dict)
    assert resolution["outcome"] == "resolved"
    assert resolution["scope_confirmed"] is None
    assert continuation_state["active_token"] is None
    assert pause_snapshot["active"] is None
    assert pause_snapshot["pending_interactions"] == []
    resolved_interactions = pause_snapshot["resolved_interactions"]
    assert isinstance(resolved_interactions, list)
    assert resolved_interactions
    assert resolved_interactions[-1]["continuation_token"] == "token-1"


def test_continuation_store_resolves_approval_denial_as_valid_outcome() -> None:
    store = ContinuationStore()
    pause_state: dict[str, object] = {}
    store.register_pending(
        pause_state, _pending_entry(continuation_token="approval-1", kind="approval")
    )

    contract, resolution, error = store.resolve_continuation(
        pause_state,
        continuation_token="approval-1",
        approve=False,
        user_input="Denied by operator.",
        resolution_payload={"resolved_by": "unit-test"},
    )

    assert error is None
    assert contract is not None
    assert contract.continuation_status == "resolved"
    assert resolution is not None
    assert resolution.approved is False
    assert resolution.outcome == "rejected"
    resolved_approvals = pause_state["resolved_approvals"]
    assert isinstance(resolved_approvals, list)
    assert resolved_approvals[-1]["resolution"]["outcome"] == "rejected"


def test_continuation_store_distinguishes_already_aborted_token() -> None:
    store = ContinuationStore()
    pause_state: dict[str, object] = {}
    store.register_pending(pause_state, _pending_entry(continuation_token="aborted-1"))
    aborted = store.abort_continuation(
        pause_state,
        continuation_token="aborted-1",
        reason="Session was cancelled.",
    )

    assert aborted is not None

    contract, resolution, error = store.resolve_continuation(
        pause_state,
        continuation_token="aborted-1",
        approve=True,
        user_input=None,
        resolution_payload={},
    )

    assert contract is not None
    assert contract.continuation_status == "aborted"
    assert resolution is None
    assert error == "already_aborted"
