from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.harness.tools.base import ToolHookContext


def _command_summary(arguments: Mapping[str, Any]) -> str | None:
    command = arguments.get("command")
    if not isinstance(command, str):
        return None
    normalized = " ".join(command.strip().split())
    if not normalized:
        return None
    return normalized[:200]


def _target_summary(arguments: Mapping[str, Any]) -> str | None:
    for key in ("agent_id", "skill_name_or_id", "profile_name", "mcp_tool_name"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _error_classification(error: Exception) -> str:
    error_type = type(error).__name__.casefold()
    if "validation" in error_type:
        return "validation"
    if "policy" in error_type or "governance" in error_type:
        return "governance"
    if "runtime" in error_type:
        return "runtime"
    if "mcp" in error_type:
        return "mcp"
    if "skill" in error_type:
        return "skill"
    return "unexpected"


def _evidence_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_count": len(payload.get("evidence_ids", []))
        if isinstance(payload.get("evidence_ids"), list)
        else 0,
        "hypothesis_count": len(payload.get("hypothesis_ids", []))
        if isinstance(payload.get("hypothesis_ids"), list)
        else 0,
        "graph_update_count": len(payload.get("graph_updates", []))
        if isinstance(payload.get("graph_updates"), list)
        else 0,
        "artifact_count": len(payload.get("artifacts", []))
        if isinstance(payload.get("artifacts"), list)
        else 0,
        "reason": payload.get("reason") if isinstance(payload.get("reason"), str) else None,
    }


class PreToolUseHook:
    async def before_execution(self, context: ToolHookContext) -> None:
        arguments = dict(getattr(context.tool_request, "arguments", {}) or {})
        decision_metadata = dict(getattr(context.decision, "metadata", {}) or {})
        normalized_arguments = {
            "command_summary": _command_summary(arguments),
            "target_summary": _target_summary(arguments),
            "argument_keys": sorted(arguments.keys()),
        }
        audit_metadata = {
            "tool_name": context.tool.name,
            "risk_level": decision_metadata.get("risk_level"),
            "mutating_target_class": decision_metadata.get("mutating_target_class"),
            "capability_tags": decision_metadata.get("capability_tags", []),
            "scope_sensitive": decision_metadata.get("scope_sensitive"),
            "evidence_effects": decision_metadata.get("evidence_effects"),
            "workflow_phase": decision_metadata.get("workflow_phase"),
            "target": decision_metadata.get("target"),
            "command_summary": normalized_arguments["command_summary"],
            "target_summary": normalized_arguments["target_summary"],
        }
        context.tool_call_metadata.update(
            {
                "normalized_arguments": normalized_arguments,
                "audit": audit_metadata,
                "governance_summary": {
                    "action": getattr(context.decision, "action", None),
                    "reason": getattr(context.decision, "reason", None),
                },
            }
        )
        context.started_payload.update(
            {
                "risk_level": audit_metadata["risk_level"],
                "mutating_target_class": audit_metadata["mutating_target_class"],
                "capability_tags": audit_metadata["capability_tags"],
                "command_summary": normalized_arguments["command_summary"],
                "target_summary": normalized_arguments["target_summary"],
            }
        )
        context.trace_entry.update(
            {
                "audit": {
                    "risk_level": audit_metadata["risk_level"],
                    "mutating_target_class": audit_metadata["mutating_target_class"],
                    "command_summary": normalized_arguments["command_summary"],
                    "target_summary": normalized_arguments["target_summary"],
                }
            }
        )

    async def after_execution(self, context: ToolHookContext) -> None:
        del context

    async def after_evidence_ingest(self, context: ToolHookContext) -> None:
        del context

    async def on_execution_error(self, context: ToolHookContext) -> None:
        del context


class PostToolUseHook:
    async def before_execution(self, context: ToolHookContext) -> None:
        del context

    async def after_execution(self, context: ToolHookContext) -> None:
        result = context.result
        if result is None:
            return
        summary = _evidence_summary(context.event_payload)
        context.transcript_result_metadata.update(
            {
                "hook_products": {
                    "status": result.status,
                    "evidence_summary": summary,
                }
            }
        )
        context.event_payload.update(
            {
                "hook_status": result.status,
                "hook_safe_summary": result.safe_summary,
            }
        )
        context.trace_entry.update(
            {
                "hook_status": result.status,
                "evidence_summary": summary,
            }
        )
        context.step_metadata.update(
            {
                "hook_products": {
                    "status": result.status,
                    "evidence_summary": summary,
                }
            }
        )

    async def after_evidence_ingest(self, context: ToolHookContext) -> None:
        del context

    async def on_execution_error(self, context: ToolHookContext) -> None:
        del context


class PostEvidenceIngestHook:
    async def before_execution(self, context: ToolHookContext) -> None:
        del context

    async def after_execution(self, context: ToolHookContext) -> None:
        del context

    async def after_evidence_ingest(self, context: ToolHookContext) -> None:
        if not context.evidence_ingest:
            return
        replanning_hint = {
            "should_replan": True,
            "reason": context.evidence_ingest.get("reason")
            or "tool observation materially changed evidence state",
            "evidence_summary": _evidence_summary(context.evidence_ingest),
        }
        context.event_payload.update(
            {
                "evidence_ids": context.evidence_ingest.get("evidence_ids", []),
                "hypothesis_ids": context.evidence_ingest.get("hypothesis_ids", []),
                "graph_updates": context.evidence_ingest.get("graph_updates", []),
                "artifacts": context.evidence_ingest.get("artifacts", []),
                "reason": context.evidence_ingest.get("reason"),
                "post_evidence_ingest": replanning_hint,
            }
        )
        context.step_metadata.update({"post_evidence_ingest": replanning_hint})
        context.trace_entry.update({"post_evidence_ingest": replanning_hint})
        context.transcript_result_metadata.update({"post_evidence_ingest": replanning_hint})

    async def on_execution_error(self, context: ToolHookContext) -> None:
        del context


class OnExecutionErrorHook:
    async def before_execution(self, context: ToolHookContext) -> None:
        del context

    async def after_execution(self, context: ToolHookContext) -> None:
        del context

    async def after_evidence_ingest(self, context: ToolHookContext) -> None:
        del context

    async def on_execution_error(self, context: ToolHookContext) -> None:
        error = context.error
        if error is None:
            return
        classification = _error_classification(error)
        error_payload = {
            "error_classification": classification,
            "error_type": type(error).__name__,
        }
        context.transcript_tool_call_metadata.update(error_payload)
        context.event_payload.update(error_payload)
        context.trace_entry.update(error_payload)
        context.step_metadata.update(error_payload)
