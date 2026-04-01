from __future__ import annotations

from typing import TypedDict


class WorkflowState(TypedDict):
    session_id: str
    current_stage: str
    stage_order: list[str]
    messages: list[dict[str, object]]
    skill_snapshot: list[dict[str, object]]
    mcp_snapshot: list[dict[str, object]]
    runtime_policy: dict[str, object]
    findings: list[dict[str, object]]
    graph_updates: list[dict[str, object]]
    seed_message_id: str | None
