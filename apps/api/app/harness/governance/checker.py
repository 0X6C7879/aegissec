from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from app.db.models import RuntimePolicy
from app.services.runtime import RuntimeService

from ..messages import ToolCallRequest
from ..tools.base import BaseTool, ToolExecutionContext, ToolRiskLevel

HarnessToolDecisionAction = Literal[
    "allow",
    "deny",
    "require_approval",
    "require_scope_confirmation",
]


@dataclass(slots=True)
class HarnessToolDecision:
    action: HarnessToolDecisionAction
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.action == "allow"


@dataclass(slots=True)
class HarnessToolDecisionRequest:
    tool_request: ToolCallRequest
    tool: BaseTool[Any]
    execution_context: ToolExecutionContext
    workflow_phase: str = "chat_turn"
    target: str | None = None
    approval_state: str | None = None
    scope_hit: bool | None = None
    scope_miss: bool | None = None


class HarnessToolDecisionChecker(Protocol):
    def evaluate(self, request: HarnessToolDecisionRequest) -> HarnessToolDecision: ...


class DefaultHarnessToolDecisionChecker:
    def evaluate(self, request: HarnessToolDecisionRequest) -> HarnessToolDecision:
        tool = request.tool
        tool_request = request.tool_request

        metadata: dict[str, Any] = {
            "tool_name": tool.name,
            "is_read_only": tool.is_read_only(),
            "risk_level": tool.risk_level().value,
            "capability_tags": list(tool.capability_tags()),
            "mutating_target_class": tool.mutating_target_class().value,
            "scope_sensitive": tool.scope_sensitive,
            "evidence_effects": tool.evidence_effects,
            "workflow_phase": request.workflow_phase,
            "target": request.target,
            "approval_state": request.approval_state,
            "scope_hit": request.scope_hit,
            "scope_miss": request.scope_miss,
        }
        if tool_request.mcp_server_id is not None:
            metadata["mcp_server_id"] = tool_request.mcp_server_id
        if tool_request.mcp_tool_name is not None:
            metadata["mcp_tool_name"] = tool_request.mcp_tool_name

        if request.scope_miss is True:
            return HarnessToolDecision(
                action="require_scope_confirmation",
                reason="Tool request appears to miss the current scope.",
                metadata=metadata,
            )

        if tool.scope_sensitive and request.scope_hit is False:
            return HarnessToolDecision(
                action="require_scope_confirmation",
                reason="Scope-sensitive tool needs an explicit scope confirmation.",
                metadata=metadata,
            )

        if tool.risk_level() == ToolRiskLevel.DESTRUCTIVE:
            return HarnessToolDecision(
                action="require_approval",
                reason="Destructive tools require explicit approval.",
                metadata=metadata,
            )

        runtime_policy_raw = request.execution_context.session.runtime_policy_json or {}
        try:
            runtime_policy = RuntimePolicy.model_validate(runtime_policy_raw)
        except ValidationError as exc:
            message = exc.errors()[0].get("msg", "Invalid runtime policy.")
            return HarnessToolDecision(
                action="deny",
                reason=f"Invalid runtime policy: {message}",
                metadata={**metadata, "runtime_policy": runtime_policy_raw},
            )

        metadata["runtime_policy"] = runtime_policy.model_dump(mode="json")

        if tool.mutating_target_class().value == "runtime":
            command = tool_request.arguments.get("command")
            timeout_seconds = tool_request.arguments.get("timeout_seconds")
            if isinstance(command, str) and command.strip():
                normalized_command = command.strip()
                metadata["command"] = normalized_command
                if len(normalized_command) > runtime_policy.max_command_length:
                    return HarnessToolDecision(
                        action="deny",
                        reason=(
                            "Command length exceeds runtime policy "
                            f"max_command_length={runtime_policy.max_command_length}."
                        ),
                        metadata=metadata,
                    )
                if (
                    isinstance(timeout_seconds, int)
                    and timeout_seconds > runtime_policy.max_execution_seconds
                ):
                    return HarnessToolDecision(
                        action="deny",
                        reason=(
                            "Requested timeout exceeds runtime policy "
                            f"max_execution_seconds={runtime_policy.max_execution_seconds}."
                        ),
                        metadata=metadata,
                    )
                if not runtime_policy.allow_network and RuntimeService._looks_like_network_command(
                    normalized_command
                ):
                    return HarnessToolDecision(
                        action="deny",
                        reason="Runtime policy blocks network-capable commands.",
                        metadata=metadata,
                    )
                if not runtime_policy.allow_write and RuntimeService._looks_like_write_command(
                    normalized_command
                ):
                    return HarnessToolDecision(
                        action="deny",
                        reason="Runtime policy blocks write-capable commands.",
                        metadata=metadata,
                    )

        return HarnessToolDecision(action="allow", metadata=metadata)
