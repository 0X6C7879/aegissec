from __future__ import annotations

import importlib
from dataclasses import dataclass, field
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
    trace_entry: dict[str, Any] = field(default_factory=dict)
    pre_hooks_applied: bool = False


@dataclass(slots=True)
class ToolExecutionErrorArtifacts:
    transcript_tool_call_metadata: dict[str, Any] = field(default_factory=dict)
    event_payload: dict[str, Any] = field(default_factory=dict)
    trace_entry: dict[str, Any] = field(default_factory=dict)
    step_metadata: dict[str, Any] = field(default_factory=dict)


def _merge_dict(target: dict[str, Any], updates: dict[str, Any]) -> None:
    if updates:
        target.update(updates)


def _materialize_evidence_products(result: Any) -> dict[str, Any]:
    event_payload = dict(getattr(result, "event_payload", {}) or {})
    semantic_deltas = list(getattr(result, "semantic_deltas", []) or [])

    evidence_ids: list[str] = []
    hypothesis_ids: list[str] = []
    graph_updates: list[dict[str, Any]] = []
    artifacts: list[Any] = []
    reasons: list[str] = []

    def add_unique_strings(target: list[str], values: object) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            if isinstance(value, str) and value not in target:
                target.append(value)

    def add_unique_artifacts(values: object) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            if value not in artifacts:
                artifacts.append(value)

    def add_graph_updates(values: object) -> None:
        if not isinstance(values, list):
            return
        for value in values:
            if isinstance(value, dict):
                normalized = dict(value)
                if normalized not in graph_updates:
                    graph_updates.append(normalized)

    add_unique_strings(evidence_ids, event_payload.get("evidence_ids"))
    add_unique_strings(hypothesis_ids, event_payload.get("hypothesis_ids"))
    add_graph_updates(event_payload.get("graph_updates"))
    add_unique_artifacts(event_payload.get("artifacts"))
    if isinstance(event_payload.get("reason"), str) and event_payload["reason"]:
        reasons.append(event_payload["reason"])

    for delta in semantic_deltas:
        if not isinstance(delta, dict):
            continue
        add_unique_strings(evidence_ids, delta.get("evidence_ids"))
        add_unique_strings(hypothesis_ids, delta.get("hypothesis_ids"))
        add_graph_updates(delta.get("graph_updates"))
        add_graph_updates(delta.get("graph_hints"))
        add_unique_artifacts(delta.get("artifacts"))
        if isinstance(delta.get("reason"), str) and delta["reason"]:
            reasons.append(delta["reason"])

    reason = reasons[0] if reasons else None
    if not any((evidence_ids, hypothesis_ids, graph_updates, artifacts, reason)):
        return {}
    return {
        "evidence_ids": evidence_ids,
        "hypothesis_ids": hypothesis_ids,
        "graph_updates": graph_updates,
        "artifacts": artifacts,
        "reason": reason,
    }


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
    terminal_session_service: Any | None = None,
    terminal_runtime_service: Any | None = None,
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
        terminal_session_service=terminal_session_service,
        terminal_runtime_service=terminal_runtime_service,
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
    elif tool_request.tool_name == "execute_terminal_command":
        started_payload.update(
            {
                "terminal_id": tool_request.arguments.get("terminal_id"),
                "command": tool_request.arguments.get("command"),
                "detach": tool_request.arguments.get("detach", False),
                "timeout_seconds": tool_request.arguments.get("timeout_seconds"),
                "artifact_paths": tool_request.arguments.get("artifact_paths", []),
            }
        )
    elif tool_request.tool_name == "read_terminal_buffer":
        started_payload.update(
            {
                "terminal_id": tool_request.arguments.get("terminal_id"),
                "job_id": tool_request.arguments.get("job_id"),
                "stream": tool_request.arguments.get("stream", "stdout"),
                "lines": tool_request.arguments.get("lines"),
            }
        )
    elif tool_request.tool_name == "stop_terminal_job":
        started_payload.update({"job_id": tool_request.arguments.get("job_id")})
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
    if not prepared.pre_hooks_applied:
        await apply_pre_tool_hooks(runtime=runtime, prepared=prepared, tool_request=tool_request)
    result = await tool.execute(prepared.execution_context, tool_request.arguments)
    after_context = tools_base.ToolHookContext(
        tool_request=tool_request,
        tool=tool,
        execution_context=prepared.execution_context,
        decision=prepared.decision,
        tool_call_metadata=dict(prepared.tool_call_metadata),
        started_payload=dict(prepared.started_payload),
        result=result,
        transcript_tool_call_metadata=dict(result.transcript_tool_call_metadata),
        transcript_result_metadata=dict(result.transcript_result_metadata),
        event_payload=dict(result.event_payload),
        trace_entry=dict(result.trace_entry),
        step_metadata=dict(result.step_metadata),
        semantic_deltas=list(result.semantic_deltas),
    )
    for hook in hooks:
        await hook.after_execution(after_context)
    _merge_dict(result.transcript_tool_call_metadata, after_context.transcript_tool_call_metadata)
    _merge_dict(result.transcript_result_metadata, after_context.transcript_result_metadata)
    _merge_dict(result.event_payload, after_context.event_payload)
    _merge_dict(result.trace_entry, after_context.trace_entry)
    _merge_dict(result.step_metadata, after_context.step_metadata)
    if after_context.semantic_deltas:
        result.semantic_deltas = [*result.semantic_deltas, *after_context.semantic_deltas]

    evidence_products = _materialize_evidence_products(result)
    if evidence_products:
        evidence_context = tools_base.ToolHookContext(
            tool_request=tool_request,
            tool=tool,
            execution_context=prepared.execution_context,
            decision=prepared.decision,
            tool_call_metadata=dict(prepared.tool_call_metadata),
            started_payload=dict(prepared.started_payload),
            result=result,
            transcript_tool_call_metadata=dict(result.transcript_tool_call_metadata),
            transcript_result_metadata=dict(result.transcript_result_metadata),
            event_payload=dict(result.event_payload),
            trace_entry=dict(result.trace_entry),
            step_metadata=dict(result.step_metadata),
            semantic_deltas=list(result.semantic_deltas),
            evidence_ingest=evidence_products,
        )
        for hook in hooks:
            await hook.after_evidence_ingest(evidence_context)
        _merge_dict(
            result.transcript_tool_call_metadata, evidence_context.transcript_tool_call_metadata
        )
        _merge_dict(result.transcript_result_metadata, evidence_context.transcript_result_metadata)
        _merge_dict(result.event_payload, evidence_context.event_payload)
        _merge_dict(result.trace_entry, evidence_context.trace_entry)
        _merge_dict(result.step_metadata, evidence_context.step_metadata)
        if evidence_context.semantic_deltas:
            result.semantic_deltas = [*result.semantic_deltas, *evidence_context.semantic_deltas]
    return result


async def apply_pre_tool_hooks(
    *,
    runtime: HarnessToolRuntime,
    prepared: PreparedToolExecution,
    tool_request: ToolCallRequest,
) -> PreparedToolExecution:
    tool = prepared.tool
    decision = prepared.decision
    if tool is None or decision is None:
        return prepared
    tools_base = importlib.import_module("app.harness.tools.base")
    hook_context = tools_base.ToolHookContext(
        tool_request=tool_request,
        tool=tool,
        execution_context=prepared.execution_context,
        decision=decision,
        tool_call_metadata=dict(prepared.tool_call_metadata),
        started_payload=dict(prepared.started_payload),
        trace_entry=dict(prepared.trace_entry),
    )
    for hook in runtime.hook_registry.iter_hooks(tool.name):
        await hook.before_execution(hook_context)
    prepared.tool_call_metadata = hook_context.tool_call_metadata
    prepared.started_payload = hook_context.started_payload
    prepared.trace_entry = hook_context.trace_entry
    prepared.pre_hooks_applied = True
    return prepared


async def notify_tool_execution_error(
    *,
    runtime: HarnessToolRuntime,
    prepared: PreparedToolExecution,
    tool_request: ToolCallRequest,
    error: Exception,
) -> ToolExecutionErrorArtifacts:
    tool = prepared.tool
    decision = prepared.decision
    if tool is None or decision is None:
        return ToolExecutionErrorArtifacts()
    tools_base = importlib.import_module("app.harness.tools.base")
    error_context = tools_base.ToolHookContext(
        tool_request=tool_request,
        tool=tool,
        execution_context=prepared.execution_context,
        decision=decision,
        tool_call_metadata=dict(prepared.tool_call_metadata),
        started_payload=dict(prepared.started_payload),
        error=error,
    )
    for hook in runtime.hook_registry.iter_hooks(tool.name):
        await hook.on_execution_error(error_context)
    return ToolExecutionErrorArtifacts(
        transcript_tool_call_metadata=error_context.transcript_tool_call_metadata,
        event_payload=error_context.event_payload,
        trace_entry=error_context.trace_entry,
        step_metadata=error_context.step_metadata,
    )
