from __future__ import annotations

from dataclasses import dataclass

from app.agent.executor import ExecutionResult
from app.db.models import TaskNode, TaskNodeStatus


@dataclass(frozen=True)
class ReflectionResult:
    conclusion: str
    failure_reason: str | None
    hypothesis_updates: list[dict[str, object]]
    replanning_suggestion: str | None
    finding: dict[str, object] | None
    evidence_confidence: float


class Reflector:
    def review(self, *, task: TaskNode, execution: ExecutionResult) -> ReflectionResult:
        if execution.status is TaskNodeStatus.COMPLETED:
            finding = {
                "id": f"finding:{task.name}",
                "title": f"{task.metadata_json.get('title', task.name)} result",
                "summary": str(
                    task.metadata_json.get("summary") or task.metadata_json.get("description") or ""
                ),
                "kind": "finding",
                "task": task.name,
                "trace_id": execution.trace_id,
                "status": "success",
                "confidence": 0.86,
                "stage_key": task.metadata_json.get("stage_key"),
                "supports": [],
                "contradicts": [],
                "validates": [],
                "causes": [],
            }
            return ReflectionResult(
                conclusion="success",
                failure_reason=None,
                hypothesis_updates=[
                    {
                        "kind": "validation",
                        "trace_id": execution.trace_id,
                        "task": task.name,
                        "result": "supported",
                    }
                ],
                replanning_suggestion=None,
                finding=finding,
                evidence_confidence=0.86,
            )

        return ReflectionResult(
            conclusion="failed",
            failure_reason="runtime_error",
            hypothesis_updates=[
                {
                    "kind": "failure",
                    "trace_id": execution.trace_id,
                    "task": task.name,
                    "result": "runtime_error",
                }
            ],
            replanning_suggestion="retry_or_replan",
            finding=None,
            evidence_confidence=0.25,
        )
