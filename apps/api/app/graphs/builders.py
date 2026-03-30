from __future__ import annotations

from app.db.models import (
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
                    "current": task.name == run.current_stage,
                },
            )
            for task in ordered_tasks
        ]
        edges: list[SessionGraphEdgeRead] = []
        for current_task, next_task in zip(ordered_tasks, ordered_tasks[1:], strict=False):
            edges.append(
                SessionGraphEdgeRead(
                    id=f"task:{current_task.id}:precedes:{next_task.id}",
                    graph_type=GraphType.TASK,
                    source=current_task.id,
                    target=next_task.id,
                    relation="precedes",
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
                data=dict(finding),
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
