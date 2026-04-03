from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.agent.tool_registry import ToolRegistry, ToolSpec, build_default_tool_registry
from app.agent.tool_runtime_models import ToolTranscriptBlock
from app.agent.workflow import WorkflowExecutionContext
from app.db.models import TaskNode, TaskNodeStatus
from app.services.capabilities import CapabilityFacade


@dataclass(frozen=True)
class ExecutionResult:
    trace_id: str
    source_type: str
    source_name: str
    command_or_action: str
    input_payload: dict[str, object]
    output_payload: dict[str, object]
    status: TaskNodeStatus
    started_at: datetime
    ended_at: datetime
    tool_name: str | None = None
    tool_category: str | None = None
    tool_capability: str | None = None
    transcript_blocks: tuple[ToolTranscriptBlock, ...] = ()
    runtime_protocol: dict[str, object] | None = None


class Executor:
    def __init__(
        self,
        capability_facade: CapabilityFacade | None = None,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self._tool_registry = tool_registry or build_default_tool_registry(capability_facade)

    def execute(self, *, context: WorkflowExecutionContext, task: TaskNode) -> ExecutionResult:
        envelope = self._tool_registry.execute_envelope(context=context, task=task)
        tool_result = envelope.runtime_result
        trace_id = tool_result.input_payload.get("trace_id")
        return ExecutionResult(
            trace_id=trace_id if isinstance(trace_id, str) else task.id,
            source_type=tool_result.source_type,
            source_name=tool_result.source_name,
            command_or_action=tool_result.command_or_action,
            input_payload=dict(tool_result.input_payload),
            output_payload=dict(tool_result.output_payload),
            status=tool_result.status,
            started_at=tool_result.started_at,
            ended_at=tool_result.ended_at,
            tool_name=tool_result.spec.name,
            tool_category=tool_result.spec.category.value,
            tool_capability=tool_result.spec.capability.value,
            transcript_blocks=envelope.transcript_blocks,
            runtime_protocol=dict(envelope.runtime_protocol),
        )

    def resolve_tool_spec(self, task: TaskNode) -> ToolSpec:
        return self._tool_registry.resolve(task=task)
