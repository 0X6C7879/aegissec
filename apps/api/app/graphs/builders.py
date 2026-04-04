from __future__ import annotations

from collections.abc import Iterable

from app.db.models import (
    GraphEdge,
    GraphNode,
    GraphType,
    SessionGraphEdgeRead,
    SessionGraphNodeRead,
    SessionGraphRead,
    TaskNode,
    TaskNodeStatus,
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
            elif graph_type is GraphType.ATTACK:
                attack_id = node.payload_json.get("attack_id")
                if isinstance(attack_id, str) and attack_id:
                    output_id = attack_id
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


class AttackGraphBuilder:
    _TASK_NODE_TYPE_MAP: dict[str, str] = {
        "context_collect.attack_surface": "surface",
        "context_collect.existing_evidence": "observation",
        "hypothesis_build.hypothesis_draft": "hypothesis",
        "safe_validation.validate_primary_hypothesis": "exploit",
        "causal_graph_update.update_causal_chain": "pivot",
        "report_export.report_summary": "outcome",
    }
    _TASK_STAGE_NODE_TYPE_MAP: dict[str, str] = {
        "context_collect": "surface",
        "hypothesis_build": "hypothesis",
        "safe_validation": "exploit",
        "findings_merge": "vulnerability",
        "causal_graph_update": "pivot",
        "report_export": "outcome",
    }
    _FINDING_NODE_TYPE_MAP: dict[str, str] = {
        "finding": "vulnerability",
        "impact": "outcome",
        "outcome": "outcome",
        "pivot": "pivot",
        "exploit": "exploit",
        "observation": "observation",
        "hypothesis": "hypothesis",
    }
    _NODE_TYPE_SORT_ORDER: dict[str, int] = {
        "goal": 0,
        "surface": 1,
        "observation": 2,
        "hypothesis": 3,
        "action": 4,
        "vulnerability": 5,
        "exploit": 6,
        "pivot": 7,
        "outcome": 8,
    }

    def build(
        self,
        *,
        run: WorkflowRun,
        tasks: list[TaskNode],
        evidence_nodes: list[GraphNode] | None = None,
        evidence_edges: list[GraphEdge] | None = None,
        causal_nodes: list[GraphNode] | None = None,
        causal_edges: list[GraphEdge] | None = None,
    ) -> SessionGraphRead:
        del evidence_edges
        ordered_tasks = sorted(tasks, key=lambda task: (task.sequence, task.created_at, task.id))
        nodes_by_id: dict[str, SessionGraphNodeRead] = {}
        edges_by_id: dict[str, SessionGraphEdgeRead] = {}
        task_ids = {task.id for task in ordered_tasks}
        task_name_to_id = {task.name: task.id for task in ordered_tasks}
        task_stage_to_id: dict[str, str] = {}
        for task in ordered_tasks:
            stage_key = task.metadata_json.get("stage_key")
            if isinstance(stage_key, str) and task.node_type.value == "stage":
                task_stage_to_id[stage_key] = task.id

        seed_message_id = run.state_json.get("seed_message_id")
        default_source_message_id = (
            str(seed_message_id)
            if isinstance(seed_message_id, str) and seed_message_id.strip()
            else None
        )

        def with_anchor_data(data: dict[str, object]) -> dict[str, object]:
            anchored = dict(data)
            if default_source_message_id is not None and not isinstance(
                anchored.get("source_message_id"), str
            ):
                anchored["source_message_id"] = default_source_message_id
            return anchored

        def add_node(*, node_id: str, node_type: str, label: str, data: dict[str, object]) -> None:
            if node_id in nodes_by_id:
                return
            nodes_by_id[node_id] = SessionGraphNodeRead(
                id=node_id,
                graph_type=GraphType.ATTACK,
                node_type=node_type,
                label=label,
                data=with_anchor_data(data),
            )

        def add_edge(
            *,
            source: str,
            target: str,
            relation: str,
            data: dict[str, object],
        ) -> None:
            if source == target or source not in nodes_by_id or target not in nodes_by_id:
                return
            edge_id = f"attack:{source}:{relation}:{target}"
            if edge_id in edges_by_id:
                return
            edges_by_id[edge_id] = SessionGraphEdgeRead(
                id=edge_id,
                graph_type=GraphType.ATTACK,
                source=source,
                target=target,
                relation=relation,
                data=data,
            )

        goal = run.state_json.get("goal")
        goal_node_id: str | None = None
        if isinstance(goal, str) and goal.strip():
            goal_node_id = f"goal:{run.id}"
            add_node(
                node_id=goal_node_id,
                node_type="goal",
                label=goal.strip(),
                data={
                    "goal": goal.strip(),
                    "run_id": run.id,
                    "session_id": run.session_id,
                    "current_stage": run.current_stage,
                    "source_graphs": ["workflow"],
                },
            )

        for task in ordered_tasks:
            depends_on = self._task_dependencies(task)
            add_node(
                node_id=task.id,
                node_type=self._task_node_type(task),
                label=str(task.metadata_json.get("title") or task.name),
                data={
                    "task_id": task.id,
                    "task_name": task.name,
                    "stage_key": task.metadata_json.get("stage_key"),
                    "status": task.status.value,
                    "sequence": task.sequence,
                    "summary": task.metadata_json.get("summary"),
                    "description": task.metadata_json.get("description"),
                    "evidence_confidence": task.metadata_json.get("evidence_confidence"),
                    "depends_on_task_ids": depends_on,
                    "requires_approval": bool(task.metadata_json.get("approval_required", False)),
                    "current": task.metadata_json.get("stage_key") == run.current_stage
                    or task.name == run.current_stage,
                    "source_graphs": ["task"],
                },
            )

        if goal_node_id is not None:
            root_tasks = [task for task in ordered_tasks if not self._task_dependencies(task)]
            for task in root_tasks:
                add_edge(
                    source=goal_node_id,
                    target=task.id,
                    relation="attempts",
                    data={"source_graphs": ["workflow", "task"]},
                )

        for task in ordered_tasks:
            depends_on = [
                dependency for dependency in self._task_dependencies(task) if dependency in task_ids
            ]
            relation = "branches_from" if len(depends_on) > 1 else "enables"
            for dependency in depends_on:
                add_edge(
                    source=dependency,
                    target=task.id,
                    relation=relation,
                    data={"source_graphs": ["task"]},
                )

        observation_ids_by_trace: dict[str, str] = {}
        if evidence_nodes:
            for node in sorted(
                evidence_nodes, key=lambda item: (item.stable_key, item.created_at, item.id)
            ):
                trace_id = node.payload_json.get("trace_id")
                observation_id = (
                    str(trace_id)
                    if isinstance(trace_id, str) and trace_id
                    else f"observation:{node.stable_key or node.id}"
                )
                status = str(node.payload_json.get("status") or "unknown")
                add_node(
                    node_id=observation_id,
                    node_type="observation",
                    label=node.label,
                    data={
                        **dict(node.payload_json),
                        "source_graphs": ["evidence"],
                    },
                )
                if isinstance(trace_id, str) and trace_id:
                    observation_ids_by_trace[trace_id] = observation_id
                task_id = node.payload_json.get("task_id")
                if isinstance(task_id, str) and task_id in task_ids:
                    add_edge(
                        source=task_id,
                        target=observation_id,
                        relation=self._execution_relation(status),
                        data={
                            "source_graphs": ["task", "evidence"],
                            "status": status,
                        },
                    )
        else:
            execution_records = run.state_json.get("execution_records", [])
            if isinstance(execution_records, list):
                for index, record in enumerate(execution_records):
                    if not isinstance(record, dict):
                        continue
                    trace_id = record.get("id")
                    observation_id = (
                        str(trace_id)
                        if isinstance(trace_id, str) and trace_id
                        else f"observation:{run.id}:{index + 1}"
                    )
                    status = str(record.get("status") or "unknown")
                    add_node(
                        node_id=observation_id,
                        node_type="observation",
                        label=str(
                            record.get("task_name")
                            or record.get("command_or_action")
                            or f"Observation {index + 1}"
                        ),
                        data={
                            **dict(record),
                            "source_graphs": ["workflow"],
                        },
                    )
                    if isinstance(trace_id, str) and trace_id:
                        observation_ids_by_trace[trace_id] = observation_id
                    task_id = record.get("task_node_id")
                    if isinstance(task_id, str) and task_id in task_ids:
                        add_edge(
                            source=task_id,
                            target=observation_id,
                            relation=self._execution_relation(status),
                            data={
                                "source_graphs": ["task", "workflow"],
                                "status": status,
                            },
                        )

        hypothesis_updates = run.state_json.get("hypothesis_updates", [])
        if isinstance(hypothesis_updates, list):
            for index, update in enumerate(hypothesis_updates):
                if not isinstance(update, dict):
                    continue
                result = str(update.get("result") or "unknown")
                trace_id = update.get("trace_id")
                task_name = update.get("task")
                hypothesis_id = (
                    f"hypothesis:{trace_id}"
                    if isinstance(trace_id, str) and trace_id
                    else (
                        f"hypothesis:{task_name}"
                        if isinstance(task_name, str) and task_name
                        else f"hypothesis:{run.id}:{index + 1}"
                    )
                )
                add_node(
                    node_id=hypothesis_id,
                    node_type="hypothesis",
                    label=str(update.get("kind") or f"Hypothesis {index + 1}"),
                    data={
                        **dict(update),
                        "source_graphs": ["workflow"],
                    },
                )
                if isinstance(task_name, str) and task_name in task_name_to_id:
                    add_edge(
                        source=task_name_to_id[task_name],
                        target=hypothesis_id,
                        relation="attempts",
                        data={"source_graphs": ["task", "workflow"]},
                    )
                if isinstance(trace_id, str) and trace_id in observation_ids_by_trace:
                    add_edge(
                        source=observation_ids_by_trace[trace_id],
                        target=hypothesis_id,
                        relation="validates" if result == "supported" else "blocks",
                        data={
                            "result": result,
                            "source_graphs": ["workflow"],
                        },
                    )

        finding_ids_by_source: dict[str, str] = {}
        causal_node_id_map: dict[str, str] = {}
        if causal_nodes:
            for node in sorted(
                causal_nodes, key=lambda item: (item.stable_key, item.created_at, item.id)
            ):
                finding_source_id = node.payload_json.get("id")
                finding_id = (
                    str(finding_source_id)
                    if isinstance(finding_source_id, str) and finding_source_id
                    else f"finding:{node.stable_key or node.id}"
                )
                causal_node_id_map[node.id] = finding_id
                add_node(
                    node_id=finding_id,
                    node_type=self._finding_node_type(node.payload_json, node.node_type),
                    label=node.label,
                    data={
                        **dict(node.payload_json),
                        "source_graphs": ["causal"],
                    },
                )
                if isinstance(finding_source_id, str) and finding_source_id:
                    finding_ids_by_source[finding_source_id] = finding_id
                trace_id = node.payload_json.get("trace_id")
                if isinstance(trace_id, str) and trace_id in observation_ids_by_trace:
                    add_edge(
                        source=observation_ids_by_trace[trace_id],
                        target=finding_id,
                        relation="confirms",
                        data={"source_graphs": ["evidence", "causal"]},
                    )
                task_name = node.payload_json.get("task")
                if isinstance(task_name, str) and task_name in task_name_to_id:
                    add_edge(
                        source=task_name_to_id[task_name],
                        target=finding_id,
                        relation="confirms",
                        data={"source_graphs": ["task", "causal"]},
                    )
            for edge in causal_edges or []:
                source_id = causal_node_id_map.get(edge.source_node_id)
                target_id = causal_node_id_map.get(edge.target_node_id)
                if source_id is None or target_id is None:
                    continue
                add_edge(
                    source=source_id,
                    target=target_id,
                    relation=edge.relation,
                    data={
                        **dict(edge.payload_json),
                        "source_graphs": ["causal"],
                    },
                )
        else:
            findings = run.state_json.get("findings", [])
            if isinstance(findings, list):
                normalized_findings = [finding for finding in findings if isinstance(finding, dict)]
                for index, finding in enumerate(normalized_findings):
                    finding_source_id = finding.get("id")
                    finding_id = (
                        str(finding_source_id)
                        if isinstance(finding_source_id, str) and finding_source_id
                        else f"finding:{run.id}:{index + 1}"
                    )
                    add_node(
                        node_id=finding_id,
                        node_type=self._finding_node_type(finding),
                        label=str(
                            finding.get("title") or finding.get("id") or f"Finding {index + 1}"
                        ),
                        data={
                            **dict(finding),
                            "source_graphs": ["workflow"],
                        },
                    )
                    if isinstance(finding_source_id, str) and finding_source_id:
                        finding_ids_by_source[finding_source_id] = finding_id
                    trace_id = finding.get("trace_id")
                    if isinstance(trace_id, str) and trace_id in observation_ids_by_trace:
                        add_edge(
                            source=observation_ids_by_trace[trace_id],
                            target=finding_id,
                            relation="confirms",
                            data={"source_graphs": ["workflow"]},
                        )
                    task_name = finding.get("task")
                    if isinstance(task_name, str) and task_name in task_name_to_id:
                        add_edge(
                            source=task_name_to_id[task_name],
                            target=finding_id,
                            relation="confirms",
                            data={"source_graphs": ["task", "workflow"]},
                        )
                for finding in normalized_findings:
                    source_key = finding.get("id")
                    if not isinstance(source_key, str) or source_key not in finding_ids_by_source:
                        continue
                    for relation in ("supports", "contradicts", "validates", "causes"):
                        raw_targets = finding.get(relation, [])
                        if not isinstance(raw_targets, list):
                            continue
                        for target_key in raw_targets:
                            if (
                                not isinstance(target_key, str)
                                or target_key not in finding_ids_by_source
                            ):
                                continue
                            add_edge(
                                source=finding_ids_by_source[source_key],
                                target=finding_ids_by_source[target_key],
                                relation=relation,
                                data={"source_graphs": ["workflow"]},
                            )

        outcome_node_id = f"outcome:{run.id}"
        outcome_relation = "confirms" if run.status.value == "done" else "blocks"
        add_node(
            node_id=outcome_node_id,
            node_type="outcome",
            label=f"Workflow {run.status.value}",
            data={
                "run_id": run.id,
                "status": run.status.value,
                "current_stage": run.current_stage,
                "last_error": run.last_error,
                "source_graphs": ["workflow"],
            },
        )
        finding_node_ids = [
            node.id
            for node in nodes_by_id.values()
            if node.node_type in {"vulnerability", "pivot", "exploit"}
        ]
        if finding_node_ids:
            for node_id in finding_node_ids:
                add_edge(
                    source=node_id,
                    target=outcome_node_id,
                    relation=outcome_relation,
                    data={"source_graphs": ["attack"]},
                )
        else:
            anchor_task_id = self._current_stage_task_id(
                ordered_tasks,
                current_stage=run.current_stage,
                task_stage_to_id=task_stage_to_id,
            )
            if anchor_task_id is not None:
                add_edge(
                    source=anchor_task_id,
                    target=outcome_node_id,
                    relation=outcome_relation,
                    data={"source_graphs": ["task", "workflow"]},
                )

        sorted_nodes = sorted(
            nodes_by_id.values(),
            key=lambda node: (
                self._NODE_TYPE_SORT_ORDER.get(node.node_type, 99),
                node.label,
                node.id,
            ),
        )
        sorted_edges = sorted(
            edges_by_id.values(),
            key=lambda edge: (edge.source, edge.relation, edge.target, edge.id),
        )
        return SessionGraphRead(
            session_id=run.session_id,
            workflow_run_id=run.id,
            graph_type=GraphType.ATTACK,
            current_stage=run.current_stage,
            nodes=sorted_nodes,
            edges=sorted_edges,
        )

    @staticmethod
    def _task_dependencies(task: TaskNode) -> list[str]:
        raw_dependencies = task.metadata_json.get("depends_on_task_ids", [])
        if not isinstance(raw_dependencies, list):
            return []
        return [dependency for dependency in raw_dependencies if isinstance(dependency, str)]

    def _task_node_type(self, task: TaskNode) -> str:
        if task.name in self._TASK_NODE_TYPE_MAP:
            return self._TASK_NODE_TYPE_MAP[task.name]
        stage_key = task.metadata_json.get("stage_key")
        if isinstance(stage_key, str) and task.node_type.value == "stage":
            return self._TASK_STAGE_NODE_TYPE_MAP.get(stage_key, "action")
        return "action"

    def _finding_node_type(self, finding: dict[str, object], fallback: str | None = None) -> str:
        kind = finding.get("kind")
        if isinstance(kind, str):
            return self._FINDING_NODE_TYPE_MAP.get(kind, kind)
        if isinstance(fallback, str) and fallback:
            return self._FINDING_NODE_TYPE_MAP.get(fallback, fallback)
        return "vulnerability"

    @staticmethod
    def _execution_relation(status: str) -> str:
        status_value = status.lower()
        if status_value in {TaskNodeStatus.FAILED.value, TaskNodeStatus.BLOCKED.value}:
            return "blocks"
        if status_value in {TaskNodeStatus.IN_PROGRESS.value, TaskNodeStatus.READY.value}:
            return "attempts"
        return "discovers"

    @staticmethod
    def _current_stage_task_id(
        tasks: Iterable[TaskNode],
        *,
        current_stage: str | None,
        task_stage_to_id: dict[str, str],
    ) -> str | None:
        if current_stage is None:
            return None
        for task in tasks:
            if task.name == current_stage:
                return task.id
        return task_stage_to_id.get(current_stage)
