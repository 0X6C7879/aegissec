from __future__ import annotations

from app.db.models import (
    GraphEdge,
    GraphNode,
    GraphType,
    SessionGraphEdgeRead,
    SessionGraphNodeRead,
    SessionGraphRead,
    TaskNode,
    WorkflowRun,
)


class TaskGraphBuilder:
    def build(self, *, run: WorkflowRun, tasks: list[TaskNode]) -> SessionGraphRead:
        ordered_tasks = sorted(tasks, key=lambda task: (task.sequence, task.created_at, task.id))
        nodes = [
            SessionGraphNodeRead(
                id=task.id,
                graph_type=GraphType.TASK,
                node_type=task.node_type.value,
                label=str(task.metadata_json.get("title") or task.name),
                data={
                    "name": task.name,
                    "status": task.status.value,
                    "sequence": task.sequence,
                    "role": task.metadata_json.get("role"),
                    "requires_approval": task.metadata_json.get("requires_approval", False),
                    "depends_on_task_ids": (
                        [dependency for dependency in depends_on_raw if isinstance(dependency, str)]
                        if isinstance(
                            (depends_on_raw := task.metadata_json.get("depends_on_task_ids", [])),
                            list,
                        )
                        else []
                    ),
                    "description": task.metadata_json.get("description"),
                    "summary": task.metadata_json.get("summary"),
                    "evidence_confidence": task.metadata_json.get("evidence_confidence"),
                    "current": task.name == run.current_stage,
                },
            )
            for task in ordered_tasks
        ]
        edges: list[SessionGraphEdgeRead] = []
        task_by_id = {task.id: task for task in ordered_tasks}
        for task in ordered_tasks:
            depends_on = task.metadata_json.get("depends_on_task_ids", [])
            if not isinstance(depends_on, list):
                depends_on = []
            for dependency in depends_on:
                if not isinstance(dependency, str) or dependency not in task_by_id:
                    continue
                edges.append(
                    SessionGraphEdgeRead(
                        id=f"task:{dependency}:depends_on:{task.id}",
                        graph_type=GraphType.TASK,
                        source=dependency,
                        target=task.id,
                        relation="depends_on",
                        data={},
                    )
                )

        return SessionGraphRead(
            session_id=run.session_id,
            workflow_run_id=run.id,
            graph_type=GraphType.TASK,
            current_stage=run.current_stage,
            nodes=nodes,
            edges=edges,
        )


class SnapshotGraphBuilder:
    def build(
        self,
        *,
        session_id: str,
        workflow_run_id: str,
        graph_type: GraphType,
        current_stage: str | None,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
    ) -> SessionGraphRead:
        node_id_map: dict[str, str] = {}
        normalized_nodes: list[SessionGraphNodeRead] = []
        for node in nodes:
            output_id = node.id
            if graph_type is GraphType.TASK:
                task_id = node.payload_json.get("task_id")
                if isinstance(task_id, str) and task_id:
                    output_id = task_id
            node_id_map[node.id] = output_id
            normalized_nodes.append(
                SessionGraphNodeRead(
                    id=output_id,
                    graph_type=node.graph_type,
                    node_type=node.node_type,
                    label=node.label,
                    data=dict(node.payload_json),
                )
            )

        return SessionGraphRead(
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            graph_type=graph_type,
            current_stage=current_stage,
            nodes=normalized_nodes,
            edges=[
                SessionGraphEdgeRead(
                    id=edge.id,
                    graph_type=edge.graph_type,
                    source=node_id_map.get(edge.source_node_id, edge.source_node_id),
                    target=node_id_map.get(edge.target_node_id, edge.target_node_id),
                    relation=edge.relation,
                    data=dict(edge.payload_json),
                )
                for edge in edges
            ],
        )


class CausalGraphBuilder:
    def build(self, *, run: WorkflowRun) -> SessionGraphRead:
        findings = run.state_json.get("findings", [])
        if not isinstance(findings, list):
            findings = []

        normalized_findings = [finding for finding in findings if isinstance(finding, dict)]
        nodes = [
            SessionGraphNodeRead(
                id=str(finding.get("id") or f"finding-{index + 1}"),
                graph_type=GraphType.CAUSAL,
                node_type=str(finding.get("kind") or "finding"),
                label=str(finding.get("title") or finding.get("id") or f"Finding {index + 1}"),
                data={
                    **dict(finding),
                    "summary": finding.get("summary"),
                    "confidence": finding.get("confidence"),
                },
            )
            for index, finding in enumerate(normalized_findings)
        ]
        node_ids = {node.id for node in nodes}
        edges: list[SessionGraphEdgeRead] = []
        for finding in normalized_findings:
            source = str(finding.get("id") or "")
            if not source:
                continue
            for relation in ("supports", "contradicts", "validates", "causes"):
                targets = finding.get(relation, [])
                if not isinstance(targets, list):
                    continue
                for raw_target in targets:
                    target = str(raw_target)
                    if target not in node_ids:
                        continue
                    edges.append(
                        SessionGraphEdgeRead(
                            id=f"causal:{source}:{relation}:{target}",
                            graph_type=GraphType.CAUSAL,
                            source=source,
                            target=target,
                            relation=relation,
                            data={},
                        )
                    )

        return SessionGraphRead(
            session_id=run.session_id,
            workflow_run_id=run.id,
            graph_type=GraphType.CAUSAL,
            current_stage=run.current_stage,
            nodes=nodes,
            edges=edges,
        )
