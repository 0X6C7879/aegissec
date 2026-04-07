from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from .messages import ChatRuntimeError, ToolCallRequest


@dataclass(slots=True)
class HarnessToolRuntime:
    available_skills: list[Any]
    decision_checker: Any
    hook_registry: Any
    tool_registry: Any


@dataclass(slots=True)
class PreparedToolExecution:
    execution_context: Any
    tool: Any | None
    decision: Any | None
    governance_metadata: dict[str, object] | None
    tool_call_metadata: dict[str, object]
    started_payload: dict[str, Any]


def build_tool_runtime(
    *,
    skill_service: Any,
    session_id: str,
    mcp_tool_inventory: list[dict[str, Any]] | None = None,
    include_swarm_tools: bool = False,
) -> HarnessToolRuntime:
    governance_checker = importlib.import_module("app.harness.governance.checker")
    tools_defaults = importlib.import_module("app.harness.tools.defaults")
    return HarnessToolRuntime(
        available_skills=skill_service.list_loaded_skills_for_agent(session_id=session_id),
        decision_checker=governance_checker.DefaultHarnessToolDecisionChecker(),
        hook_registry=tools_defaults.build_default_tool_hook_registry(),
        tool_registry=tools_defaults.build_default_tool_registry(
            mcp_tools=mcp_tool_inventory,
            include_swarm_tools=include_swarm_tools,
        ),
    )


def prepare_tool_execution(
    *,
    runtime: HarnessToolRuntime,
    tool_request: ToolCallRequest,
    session: Any,
    assistant_message: Any,
    runtime_service: Any,
    skill_service: Any,
    mcp_service: Any,
    session_state: Any | None = None,
    swarm_coordinator: Any | None = None,
) -> PreparedToolExecution:
    tools_base = importlib.import_module("app.harness.tools.base")
    governance_checker = importlib.import_module("app.harness.governance.checker")
    execution_context = tools_base.ToolExecutionContext(
        session=session,
        assistant_message=assistant_message,
        runtime_service=runtime_service,
        skill_service=skill_service,
        mcp_service=mcp_service,
        available_skills=runtime.available_skills,
        session_state=session_state,
        swarm_coordinator=swarm_coordinator,
    )
    tool = runtime.tool_registry.get(tool_request.tool_name)
    decision = (
        runtime.decision_checker.evaluate(
            governance_checker.HarnessToolDecisionRequest(
                tool_request=tool_request,
                tool=tool,
                execution_context=execution_context,
            )
        )
        if tool is not None
        else None
    )
    governance_metadata: dict[str, object] | None = (
        {
            "action": decision.action,
            "reason": decision.reason,
            "metadata": dict(decision.metadata),
        }
        if decision is not None
        else None
    )
    tool_call_metadata: dict[str, object] = {"arguments": dict(tool_request.arguments)}
    if governance_metadata is not None:
        tool_call_metadata["governance"] = governance_metadata
    started_payload: dict[str, Any] = {
        "tool": tool_request.tool_name,
        "tool_call_id": tool_request.tool_call_id,
        "arguments": tool_request.arguments,
        "message_id": assistant_message.id,
        "assistant_message_id": assistant_message.id,
    }
    if assistant_message.generation_id is not None:
        started_payload["generation_id"] = assistant_message.generation_id
    if tool_request.tool_name == "execute_kali_command":
        started_payload.update(
            {
                "command": tool_request.arguments.get("command"),
                "timeout_seconds": tool_request.arguments.get("timeout_seconds"),
                "artifact_paths": tool_request.arguments.get("artifact_paths", []),
            }
        )
    if tool_request.mcp_server_id is not None and tool_request.mcp_tool_name is not None:
        started_payload.update(
            {
                "mcp_server_id": tool_request.mcp_server_id,
                "mcp_tool_name": tool_request.mcp_tool_name,
            }
        )
    return PreparedToolExecution(
        execution_context=execution_context,
        tool=tool,
        decision=decision,
        governance_metadata=governance_metadata,
        tool_call_metadata=tool_call_metadata,
        started_payload=started_payload,
    )


async def run_tool_with_hooks(
    *,
    runtime: HarnessToolRuntime,
    prepared: PreparedToolExecution,
    tool_request: ToolCallRequest,
) -> Any:
    tool = prepared.tool
    if tool is None:
        raise ChatRuntimeError(f"Unsupported tool requested: {tool_request.tool_name}.")
    if prepared.decision is None:
        raise ChatRuntimeError("Missing governance decision for tool execution.")
    tools_base = importlib.import_module("app.harness.tools.base")
    hooks = list(runtime.hook_registry.iter_hooks(tool.name))
    before_context = tools_base.ToolHookContext(
        tool_request=tool_request,
        tool=tool,
        execution_context=prepared.execution_context,
        decision=prepared.decision,
    )
    for hook in hooks:
        await hook.before_execution(before_context)
    result = await tool.execute(prepared.execution_context, tool_request.arguments)
    after_context = tools_base.ToolHookContext(
        tool_request=tool_request,
        tool=tool,
        execution_context=prepared.execution_context,
        decision=prepared.decision,
        result=result,
    )
    for hook in hooks:
        await hook.after_execution(after_context)
    return result


async def notify_tool_execution_error(
    *,
    runtime: HarnessToolRuntime,
    prepared: PreparedToolExecution,
    tool_request: ToolCallRequest,
    error: Exception,
) -> None:
    tool = prepared.tool
    decision = prepared.decision
    if tool is None or decision is None:
        return
    tools_base = importlib.import_module("app.harness.tools.base")
    error_context = tools_base.ToolHookContext(
        tool_request=tool_request,
        tool=tool,
        execution_context=prepared.execution_context,
        decision=decision,
        error=error,
    )
    for hook in runtime.hook_registry.iter_hooks(tool.name):
        await hook.on_execution_error(error_context)
