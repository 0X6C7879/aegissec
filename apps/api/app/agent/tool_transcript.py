from __future__ import annotations

from app.agent.tool_registry import ToolExecutionRequest, ToolSpec
from app.agent.tool_runtime_models import ToolTranscriptBlock
from app.db.models import TaskNodeStatus


def build_tool_transcript_blocks(
    *,
    request: ToolExecutionRequest,
    spec: ToolSpec,
    input_payload: dict[str, object],
    output_payload: dict[str, object],
    status: TaskNodeStatus,
    runtime_protocol: dict[str, object],
) -> tuple[ToolTranscriptBlock, ...]:
    blocks = [
        ToolTranscriptBlock(
            kind="tool_use",
            content=spec.render_tool_use_message(request=request, input_payload=input_payload),
            metadata={
                "tool_name": spec.name,
                "task_name": request.task.name,
                "status": status.value,
            },
        )
    ]
    if (
        output_payload.get("policy_denied") is True
        or output_payload.get("validation_failed") is True
        or output_payload.get("execution_blocked") is True
    ):
        blocks.append(
            ToolTranscriptBlock(
                kind="tool_error",
                content=spec.render_tool_error_message(
                    request=request,
                    input_payload=input_payload,
                    output_payload=output_payload,
                ),
                metadata={
                    "tool_name": spec.name,
                    "task_name": request.task.name,
                    "status": status.value,
                },
            )
        )
    else:
        blocks.append(
            ToolTranscriptBlock(
                kind="tool_result",
                content=spec.render_tool_result_message(
                    request=request,
                    input_payload=input_payload,
                    output_payload=output_payload,
                ),
                metadata={
                    "tool_name": spec.name,
                    "task_name": request.task.name,
                    "status": status.value,
                },
            )
        )
    blocks.append(
        ToolTranscriptBlock(
            kind="tool_protocol",
            content="",
            metadata=dict(runtime_protocol),
            is_metadata_only=True,
        )
    )
    return tuple(blocks)
