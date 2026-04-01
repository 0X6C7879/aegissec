from __future__ import annotations

from app.agent.executor import ExecutionResult
from app.agent.reflector import ReflectionResult
from app.db.models import GraphType, TaskNode, WorkflowRun
from app.db.repositories import GraphRepository


class GraphManager:
    def __init__(self, graph_repository: GraphRepository) -> None:
        self._graph_repository = graph_repository

    def sync_task_graph(self, *, run: WorkflowRun, tasks: list[TaskNode]) -> None:
        for task in tasks:
            stable_key = f"task-node:{task.id}"
            node = self._graph_repository.get_node_by_stable_key(
                run.session_id,
                stable_key,
                workflow_run_id=run.id,
            )
            depends_on_raw = task.metadata_json.get("depends_on_task_ids", [])
            depends_on = (
                [dependency for dependency in depends_on_raw if isinstance(dependency, str)]
                if isinstance(depends_on_raw, list)
                else []
            )
            payload: dict[str, object] = {
                "task_id": task.id,
                "name": task.name,
                "status": task.status.value,
                "sequence": task.sequence,
                "role": task.metadata_json.get("role"),
                "stage_key": task.metadata_json.get("stage_key"),
                "priority": task.metadata_json.get("priority"),
                "summary": task.metadata_json.get("summary"),
                "evidence_confidence": task.metadata_json.get("evidence_confidence"),
                "current": task.metadata_json.get("stage_key") == run.current_stage,
                "depends_on_task_ids": depends_on,
                "requires_approval": bool(task.metadata_json.get("approval_required", False)),
            }
            if node is None:
                self._graph_repository.create_node(
                    session_id=run.session_id,
                    workflow_run_id=run.id,
                    graph_type=GraphType.TASK,
                    node_type=task.node_type.value,
                    label=str(task.metadata_json.get("title") or task.name),
                    payload=payload,
                    stable_key=stable_key,
                )
            else:
                self._graph_repository.patch_node(
                    node,
                    label=str(task.metadata_json.get("title") or task.name),
                    payload=payload,
                )

        task_node_lookup = {
            task.id: self._graph_repository.get_node_by_stable_key(
                run.session_id,
                f"task-node:{task.id}",
                workflow_run_id=run.id,
            )
            for task in tasks
        }

        for task in tasks:
            depends_on_raw = task.metadata_json.get("depends_on_task_ids", [])
            depends_on = (
                [dependency for dependency in depends_on_raw if isinstance(dependency, str)]
                if isinstance(depends_on_raw, list)
                else []
            )
            for dependency in depends_on:
                source = task_node_lookup.get(dependency)
                target = task_node_lookup.get(task.id)
                if source is None or target is None:
                    continue
                stable_key = f"task-edge:{source.id}:depends_on:{target.id}"
                existing = self._graph_repository.get_edge_by_stable_key(
                    run.session_id,
                    stable_key,
                    workflow_run_id=run.id,
                )
                if existing is not None:
                    continue
                self._graph_repository.create_edge(
                    session_id=run.session_id,
                    workflow_run_id=run.id,
                    graph_type=GraphType.TASK,
                    source_node_id=source.id,
                    target_node_id=target.id,
                    relation="depends_on",
                    payload={},
                    stable_key=stable_key,
                )

    def record_execution(
        self,
        *,
        run: WorkflowRun,
        task: TaskNode,
        execution: ExecutionResult,
        reflection: ReflectionResult,
    ) -> None:
        evidence_stable_key = f"evidence-node:{execution.trace_id}"
        evidence_node = self._graph_repository.get_node_by_stable_key(
            run.session_id,
            evidence_stable_key,
            workflow_run_id=run.id,
        )
        if evidence_node is None:
            evidence_node = self._graph_repository.create_node(
                session_id=run.session_id,
                workflow_run_id=run.id,
                graph_type=GraphType.EVIDENCE,
                node_type="execution",
                label=str(task.metadata_json.get("title") or task.name),
                payload={
                    "trace_id": execution.trace_id,
                    "task_id": task.id,
                    "task_name": task.name,
                    "stage_key": task.metadata_json.get("stage_key"),
                    "source_type": execution.source_type,
                    "source_name": execution.source_name,
                    "command_or_action": execution.command_or_action,
                    "status": execution.status.value,
                    "summary": task.metadata_json.get("summary"),
                    "confidence": reflection.evidence_confidence,
                    "input": dict(execution.input_payload),
                    "output": dict(execution.output_payload),
                    "started_at": execution.started_at.isoformat(),
                    "ended_at": execution.ended_at.isoformat(),
                },
                stable_key=evidence_stable_key,
            )

        finding = reflection.finding
        if finding is None:
            return
        finding_stable_key = f"causal-node:{finding['id']}"
        causal_node = self._graph_repository.get_node_by_stable_key(
            run.session_id,
            finding_stable_key,
            workflow_run_id=run.id,
        )
        if causal_node is None:
            causal_node = self._graph_repository.create_node(
                session_id=run.session_id,
                workflow_run_id=run.id,
                graph_type=GraphType.CAUSAL,
                node_type=str(finding.get("kind") or "finding"),
                label=str(finding.get("title") or finding.get("id")),
                payload=dict(finding),
                stable_key=finding_stable_key,
            )

        relation_key = f"causal-edge:{evidence_node.id}:supports:{causal_node.id}"
        if (
            self._graph_repository.get_edge_by_stable_key(
                run.session_id,
                relation_key,
                workflow_run_id=run.id,
            )
            is None
        ):
            self._graph_repository.create_edge(
                session_id=run.session_id,
                workflow_run_id=run.id,
                graph_type=GraphType.CAUSAL,
                source_node_id=evidence_node.id,
                target_node_id=causal_node.id,
                relation="supports",
                payload={"trace_id": execution.trace_id},
                stable_key=relation_key,
            )
