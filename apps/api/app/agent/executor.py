from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

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


class Executor:
    def __init__(self, capability_facade: CapabilityFacade | None = None) -> None:
        self._capability_facade = capability_facade

    def execute(self, *, context: WorkflowExecutionContext, task: TaskNode) -> ExecutionResult:
        started_at = datetime.now(UTC)
        trace_id = f"trace-{uuid4()}"
        task_kind = str(task.metadata_json.get("kind") or "task")
        source_type = "coordinator" if task_kind == "stage" else "runtime"
        source_name = "workflow-engine" if task_kind == "stage" else "authorized-assessment"

        if task_kind == "stage":
            action = f"transition:{task.name}"
            output_payload: dict[str, object] = {
                "stage": task.name,
                "status": "entered",
                "note": "stage_transition_recorded",
            }
            status = TaskNodeStatus.COMPLETED
        else:
            action = f"execute:{task.name}"
            capability_snapshot: dict[str, object] = {}
            if (
                task.name == "skill_mcp_sync.capability_snapshot"
                and self._capability_facade is not None
            ):
                capability_snapshot = self._capability_facade.build_snapshot()
            observation: dict[str, object] = {
                "task": task.name,
                "goal": context.goal,
                "stage": task.metadata_json.get("stage_key"),
                "observation": f"Structured execution completed for {task.name}.",
            }
            output_payload = {
                "stdout": f"{task.name} completed under authorized assessment policy.",
                "stderr": "",
                "exit_code": 0,
                "capability_snapshot": capability_snapshot,
                "artifacts": [
                    {
                        "type": "log",
                        "name": f"{task.name}.json",
                        "trace_id": trace_id,
                    }
                ],
                "observations": [observation],
            }
            status = TaskNodeStatus.COMPLETED

        ended_at = datetime.now(UTC)
        return ExecutionResult(
            trace_id=trace_id,
            source_type=source_type,
            source_name=source_name,
            command_or_action=action,
            input_payload={
                "session_id": context.session_id,
                "workflow_run_id": context.workflow_run_id,
                "task_id": task.id,
                "task_name": task.name,
                "stage_key": task.metadata_json.get("stage_key"),
                "role": task.metadata_json.get("role"),
                "role_prompt": task.metadata_json.get("role_prompt"),
                "sub_agent_role_prompt": task.metadata_json.get("sub_agent_role_prompt"),
                "runtime_policy": dict(context.runtime_policy),
            },
            output_payload=output_payload,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
        )
