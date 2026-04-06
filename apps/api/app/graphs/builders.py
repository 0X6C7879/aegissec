from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from app.db.models import (
    ChatGeneration,
    GraphEdge,
    GraphNode,
    GraphType,
    Message,
    MessageRole,
    Session,
    SessionGraphEdgeRead,
    SessionGraphNodeRead,
    SessionGraphRead,
    TaskNode,
    TaskNodeStatus,
    WorkflowRun,
    WorkflowRunStatus,
    resolve_message_assistant_transcript,
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

        normalized_edges = [
            SessionGraphEdgeRead(
                id=edge.id,
                graph_type=edge.graph_type,
                source=node_id_map.get(edge.source_node_id, edge.source_node_id),
                target=node_id_map.get(edge.target_node_id, edge.target_node_id),
                relation=edge.relation,
                data=dict(edge.payload_json),
            )
            for edge in edges
        ]
        if graph_type is GraphType.ATTACK:
            attack_graph_builder = AttackGraphBuilder()
            pruned_nodes_by_id, pruned_edges_by_id = (
                attack_graph_builder._prune_attack_graph_for_default_view(
                    nodes_by_id={node.id: node for node in normalized_nodes},
                    edges_by_id={edge.id: edge for edge in normalized_edges},
                )
            )
            normalized_nodes = sorted(
                pruned_nodes_by_id.values(),
                key=lambda node: (
                    attack_graph_builder._NODE_TYPE_SORT_ORDER.get(node.node_type, 99),
                    node.label,
                    node.id,
                ),
            )
            normalized_edges = sorted(
                pruned_edges_by_id.values(),
                key=lambda edge: (edge.source, edge.relation, edge.target, edge.id),
            )

        return SessionGraphRead(
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            graph_type=graph_type,
            current_stage=current_stage,
            nodes=normalized_nodes,
            edges=normalized_edges,
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
    _THINK_TAG_RE = re.compile(r"</?think\b[^>]*>", re.IGNORECASE)
    _SEMANTIC_ATTACK_CORE_NODE_TYPES = frozenset(
        {"goal", "surface", "vulnerability", "exploit", "pivot", "outcome"}
    )
    _PRESERVED_ATTACK_STATUS_VALUES = frozenset({"in_progress", "blocked", "failed"})
    _OBSERVATION_KEEP_NEIGHBOR_TYPES = frozenset({"vulnerability", "exploit", "pivot", "outcome"})
    _HYPOTHESIS_KEEP_NEIGHBOR_TYPES = frozenset({"vulnerability", "exploit", "pivot", "outcome"})
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
        outcome_relation = self._workflow_outcome_relation(run.status)
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

        nodes_by_id, edges_by_id = self._prune_attack_graph_for_default_view(
            nodes_by_id=nodes_by_id,
            edges_by_id=edges_by_id,
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

    def _prune_attack_graph_for_default_view(
        self,
        *,
        nodes_by_id: dict[str, SessionGraphNodeRead],
        edges_by_id: dict[str, SessionGraphEdgeRead],
    ) -> tuple[dict[str, SessionGraphNodeRead], dict[str, SessionGraphEdgeRead]]:
        all_edges = tuple(edges_by_id.values())
        incoming_edges_by_node, outgoing_edges_by_node = self._index_attack_edges(all_edges)
        keep_node_ids = self._collect_attack_graph_keep_node_ids(
            nodes_by_id=nodes_by_id,
            incoming_edges_by_node=incoming_edges_by_node,
            outgoing_edges_by_node=outgoing_edges_by_node,
        )
        removable_node_ids = {
            node.id
            for node in nodes_by_id.values()
            if not self._should_keep_attack_node(
                node=node,
                keep_node_ids=keep_node_ids,
                edges=all_edges,
            )
        }
        if not removable_node_ids:
            return nodes_by_id, edges_by_id

        pruned_edges_by_id = {
            edge_id: edge
            for edge_id, edge in edges_by_id.items()
            if edge.source not in removable_node_ids and edge.target not in removable_node_ids
        }
        for node_id in removable_node_ids:
            incoming_edges = incoming_edges_by_node.get(node_id, [])
            outgoing_edges = outgoing_edges_by_node.get(node_id, [])
            for incoming_edge in incoming_edges:
                if incoming_edge.source in removable_node_ids:
                    continue
                for outgoing_edge in outgoing_edges:
                    if outgoing_edge.target in removable_node_ids:
                        continue
                    if incoming_edge.source == outgoing_edge.target:
                        continue
                    bridge_relation = self._bridge_attack_edge_relation(
                        incoming_relation=incoming_edge.relation,
                        outgoing_relation=outgoing_edge.relation,
                    )
                    bridge_edge_id = (
                        f"attack:{incoming_edge.source}:{bridge_relation}:{outgoing_edge.target}"
                    )
                    if bridge_edge_id in pruned_edges_by_id:
                        continue
                    pruned_edges_by_id[bridge_edge_id] = SessionGraphEdgeRead(
                        id=bridge_edge_id,
                        graph_type=GraphType.ATTACK,
                        source=incoming_edge.source,
                        target=outgoing_edge.target,
                        relation=bridge_relation,
                        data=self._merge_attack_edge_data(incoming_edge, outgoing_edge),
                    )

        pruned_nodes_by_id = {
            node_id: node
            for node_id, node in nodes_by_id.items()
            if node_id not in removable_node_ids
        }
        pruned_edges_by_id = {
            edge_id: edge
            for edge_id, edge in pruned_edges_by_id.items()
            if edge.source in pruned_nodes_by_id and edge.target in pruned_nodes_by_id
        }
        return pruned_nodes_by_id, pruned_edges_by_id

    def _collect_attack_graph_keep_node_ids(
        self,
        *,
        nodes_by_id: dict[str, SessionGraphNodeRead],
        incoming_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
        outgoing_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
    ) -> set[str]:
        path_node_ids = self._collect_attack_path_node_ids(
            nodes_by_id=nodes_by_id,
            incoming_edges_by_node=incoming_edges_by_node,
            outgoing_edges_by_node=outgoing_edges_by_node,
        )
        keep_node_ids = {
            node.id
            for node in nodes_by_id.values()
            if node.node_type in self._SEMANTIC_ATTACK_CORE_NODE_TYPES
            or self._node_has_preserved_status(node)
            or (node.id in path_node_ids and node.node_type != "action")
        }
        kept_hypothesis_ids = {
            node.id
            for node in nodes_by_id.values()
            if node.node_type == "hypothesis"
            and self._hypothesis_node_should_be_kept(
                node=node,
                nodes_by_id=nodes_by_id,
                incoming_edges_by_node=incoming_edges_by_node,
                outgoing_edges_by_node=outgoing_edges_by_node,
                path_node_ids=path_node_ids,
            )
        }
        keep_node_ids.update(kept_hypothesis_ids)
        keep_node_ids.update(
            node.id
            for node in nodes_by_id.values()
            if node.node_type == "observation"
            and self._observation_node_should_be_kept(
                node=node,
                nodes_by_id=nodes_by_id,
                incoming_edges_by_node=incoming_edges_by_node,
                outgoing_edges_by_node=outgoing_edges_by_node,
                path_node_ids=path_node_ids,
                kept_hypothesis_ids=kept_hypothesis_ids,
            )
        )
        return keep_node_ids

    def _collect_attack_path_node_ids(
        self,
        *,
        nodes_by_id: dict[str, SessionGraphNodeRead],
        incoming_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
        outgoing_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
    ) -> set[str]:
        goal_node_ids = [node.id for node in nodes_by_id.values() if node.node_type == "goal"]
        target_node_ids = [
            node.id
            for node in nodes_by_id.values()
            if node.node_type in {"vulnerability", "exploit", "pivot", "outcome"}
        ]
        if not goal_node_ids or not target_node_ids:
            return set()
        reachable_from_goal_ids = self._walk_attack_graph(
            start_ids=goal_node_ids,
            edges_by_node=outgoing_edges_by_node,
            reverse=False,
        )
        reaches_target_ids = self._walk_attack_graph(
            start_ids=target_node_ids,
            edges_by_node=incoming_edges_by_node,
            reverse=True,
        )
        return reachable_from_goal_ids & reaches_target_ids

    def _walk_attack_graph(
        self,
        *,
        start_ids: Iterable[str],
        edges_by_node: dict[str, list[SessionGraphEdgeRead]],
        reverse: bool,
    ) -> set[str]:
        visited: set[str] = set()
        pending = [node_id for node_id in start_ids if node_id]
        while pending:
            node_id = pending.pop()
            if node_id in visited:
                continue
            visited.add(node_id)
            for edge in edges_by_node.get(node_id, []):
                neighbor_id = edge.source if reverse else edge.target
                if neighbor_id not in visited:
                    pending.append(neighbor_id)
        return visited

    def _index_attack_edges(
        self,
        edges: Iterable[SessionGraphEdgeRead],
    ) -> tuple[dict[str, list[SessionGraphEdgeRead]], dict[str, list[SessionGraphEdgeRead]]]:
        incoming_edges_by_node: dict[str, list[SessionGraphEdgeRead]] = {}
        outgoing_edges_by_node: dict[str, list[SessionGraphEdgeRead]] = {}
        for edge in edges:
            incoming_edges_by_node.setdefault(edge.target, []).append(edge)
            outgoing_edges_by_node.setdefault(edge.source, []).append(edge)
        return incoming_edges_by_node, outgoing_edges_by_node

    def _should_keep_attack_node(
        self,
        *,
        node: SessionGraphNodeRead,
        keep_node_ids: set[str],
        edges: Iterable[SessionGraphEdgeRead],
    ) -> bool:
        if node.node_type == "action":
            return self._node_has_preserved_status(node) or self._node_is_required_terminal(
                node, edges
            )
        if node.node_type in {"observation", "hypothesis"}:
            return node.id in keep_node_ids
        return True

    def _observation_node_should_be_kept(
        self,
        *,
        node: SessionGraphNodeRead,
        nodes_by_id: dict[str, SessionGraphNodeRead],
        incoming_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
        outgoing_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
        path_node_ids: set[str],
        kept_hypothesis_ids: set[str],
    ) -> bool:
        if self._node_has_preserved_status(node) or node.id in path_node_ids:
            return True
        if self._node_has_attack_neighbor_types(
            node_id=node.id,
            nodes_by_id=nodes_by_id,
            incoming_edges_by_node=incoming_edges_by_node,
            outgoing_edges_by_node=outgoing_edges_by_node,
            neighbor_types=self._OBSERVATION_KEEP_NEIGHBOR_TYPES,
        ):
            return True
        return self._node_has_attack_neighbor_ids(
            node_id=node.id,
            incoming_edges_by_node=incoming_edges_by_node,
            outgoing_edges_by_node=outgoing_edges_by_node,
            neighbor_ids=kept_hypothesis_ids,
        )

    def _hypothesis_node_should_be_kept(
        self,
        *,
        node: SessionGraphNodeRead,
        nodes_by_id: dict[str, SessionGraphNodeRead],
        incoming_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
        outgoing_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
        path_node_ids: set[str],
    ) -> bool:
        if self._node_has_preserved_status(node) or node.id in path_node_ids:
            return True
        if self._node_has_attack_neighbor_types(
            node_id=node.id,
            nodes_by_id=nodes_by_id,
            incoming_edges_by_node=incoming_edges_by_node,
            outgoing_edges_by_node=outgoing_edges_by_node,
            neighbor_types=self._HYPOTHESIS_KEEP_NEIGHBOR_TYPES,
        ):
            return True
        for edge in incoming_edges_by_node.get(node.id, []):
            if edge.relation not in {"validates", "blocks"}:
                continue
            source_node = nodes_by_id.get(edge.source)
            if source_node is not None and source_node.node_type == "observation":
                return True
        return False

    def _node_has_attack_neighbor_types(
        self,
        *,
        node_id: str,
        nodes_by_id: dict[str, SessionGraphNodeRead],
        incoming_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
        outgoing_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
        neighbor_types: frozenset[str],
    ) -> bool:
        for edge in incoming_edges_by_node.get(node_id, []):
            source_node = nodes_by_id.get(edge.source)
            if source_node is not None and source_node.node_type in neighbor_types:
                return True
        for edge in outgoing_edges_by_node.get(node_id, []):
            target_node = nodes_by_id.get(edge.target)
            if target_node is not None and target_node.node_type in neighbor_types:
                return True
        return False

    def _node_has_attack_neighbor_ids(
        self,
        *,
        node_id: str,
        incoming_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
        outgoing_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
        neighbor_ids: set[str],
    ) -> bool:
        for edge in incoming_edges_by_node.get(node_id, []):
            if edge.source in neighbor_ids:
                return True
        for edge in outgoing_edges_by_node.get(node_id, []):
            if edge.target in neighbor_ids:
                return True
        return False

    def _node_is_active(self, node: SessionGraphNodeRead) -> bool:
        status = node.data.get("status")
        return bool(node.data.get("current") or node.data.get("active") or status == "in_progress")

    def _node_has_preserved_status(self, node: SessionGraphNodeRead) -> bool:
        status = node.data.get("status")
        return self._node_is_active(node) or status in self._PRESERVED_ATTACK_STATUS_VALUES

    def _node_is_required_terminal(
        self,
        node: SessionGraphNodeRead,
        edges: Iterable[SessionGraphEdgeRead],
    ) -> bool:
        incoming_count = 0
        outgoing_count = 0
        for edge in edges:
            if edge.target == node.id:
                incoming_count += 1
            if edge.source == node.id:
                outgoing_count += 1
        return incoming_count == 0 or outgoing_count == 0

    def _bridge_attack_edge_relation(
        self, *, incoming_relation: str, outgoing_relation: str
    ) -> str:
        if outgoing_relation != "attempts":
            return outgoing_relation
        return incoming_relation

    def _merge_attack_edge_data(
        self,
        incoming_edge: SessionGraphEdgeRead,
        outgoing_edge: SessionGraphEdgeRead,
    ) -> dict[str, object]:
        merged: dict[str, object] = {
            **dict(incoming_edge.data),
            **dict(outgoing_edge.data),
        }
        source_graphs: list[str] = []
        for raw_value in (
            incoming_edge.data.get("source_graphs"),
            outgoing_edge.data.get("source_graphs"),
        ):
            if not isinstance(raw_value, list):
                continue
            for item in raw_value:
                if isinstance(item, str) and item not in source_graphs:
                    source_graphs.append(item)
        if source_graphs:
            merged["source_graphs"] = source_graphs
        return merged

    def _workflow_outcome_relation(self, status: WorkflowRunStatus) -> str:
        if status is WorkflowRunStatus.DONE:
            return "confirms"
        if status in {WorkflowRunStatus.BLOCKED, WorkflowRunStatus.ERROR}:
            return "blocks"
        return "attempts"

    def build_from_conversation(
        self,
        *,
        session: Session,
        messages: list[Message],
        generations: list[ChatGeneration],
    ) -> SessionGraphRead:
        nodes_by_id: dict[str, SessionGraphNodeRead] = {}
        edges_by_id: dict[str, SessionGraphEdgeRead] = {}
        generation_by_assistant_message_id = {
            generation.assistant_message_id: generation for generation in generations
        }
        ordered_messages = sorted(
            messages,
            key=lambda message: (message.sequence, message.created_at, message.id),
        )
        if not ordered_messages and not (session.goal or "").strip():
            return SessionGraphRead(
                session_id=session.id,
                workflow_run_id="",
                graph_type=GraphType.ATTACK,
                current_stage=None,
                nodes=[],
                edges=[],
            )
        first_user_message = next(
            (message for message in ordered_messages if message.role == MessageRole.USER),
            None,
        )
        goal_text = (
            session.goal
            or (first_user_message.content.strip() if first_user_message is not None else "")
            or session.title
        ).strip()

        def add_node(*, node_id: str, node_type: str, label: str, data: dict[str, object]) -> None:
            if node_id in nodes_by_id:
                return
            nodes_by_id[node_id] = SessionGraphNodeRead(
                id=node_id,
                graph_type=GraphType.ATTACK,
                node_type=node_type,
                label=label,
                data=dict(data),
            )

        def add_edge(*, source: str, target: str, relation: str, data: dict[str, object]) -> None:
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
                data=dict(data),
            )

        goal_node_id = "goal:conversation"
        if goal_text:
            add_node(
                node_id=goal_node_id,
                node_type="goal",
                label=self._truncate_label(goal_text, fallback="Conversation goal"),
                data={
                    "goal": goal_text,
                    "session_id": session.id,
                    "source_graphs": ["conversation"],
                },
            )

        latest_anchor_node_id = goal_node_id if goal_text else None
        for message in ordered_messages:
            if message.role != MessageRole.ASSISTANT:
                continue

            generation = generation_by_assistant_message_id.get(message.id)
            transcript = resolve_message_assistant_transcript(message)
            message_action_id = f"action:message:{message.id}"
            add_node(
                node_id=message_action_id,
                node_type="action",
                label=self._conversation_action_label(message=message, generation=generation),
                data={
                    "message_id": message.id,
                    "generation_id": generation.id if generation is not None else None,
                    "generation_action": (
                        generation.action.value if generation is not None else None
                    ),
                    "status": (
                        generation.status.value
                        if generation is not None
                        else (message.status.value if message.status is not None else None)
                    ),
                    "source_graphs": ["conversation", "generation"],
                },
            )
            if latest_anchor_node_id is not None:
                add_edge(
                    source=latest_anchor_node_id,
                    target=message_action_id,
                    relation="attempts",
                    data={"source_graphs": ["conversation"]},
                )

            tool_action_ids: dict[str, str] = {}
            seen_hypothesis_keys: set[str] = set()
            latest_message_anchor_id = message_action_id
            latest_message_observation_id: str | None = None

            for segment in transcript:
                if segment.kind == "tool_call":
                    action_id = f"action:tool:{segment.tool_call_id or segment.id}"
                    add_node(
                        node_id=action_id,
                        node_type=self._conversation_tool_node_type(segment),
                        label=self._conversation_tool_action_label(segment),
                        data={
                            "message_id": message.id,
                            "tool_call_id": segment.tool_call_id,
                            "tool_name": segment.tool_name,
                            "arguments": dict(segment.metadata_payload),
                            "command": self._read_segment_command(segment),
                            "source_graphs": ["conversation", "transcript"],
                        },
                    )
                    add_edge(
                        source=message_action_id,
                        target=action_id,
                        relation="attempts",
                        data={"source_graphs": ["conversation", "transcript"]},
                    )
                    if isinstance(segment.tool_call_id, str) and segment.tool_call_id:
                        tool_action_ids[segment.tool_call_id] = action_id
                    latest_message_anchor_id = action_id
                    continue

                if segment.kind == "reasoning":
                    reasoning_text = self._extract_reasoning_text(segment.text)
                    if reasoning_text:
                        hypothesis_key = reasoning_text.casefold()
                        if hypothesis_key not in seen_hypothesis_keys:
                            seen_hypothesis_keys.add(hypothesis_key)
                            hypothesis_id = f"hypothesis:{message.id}:{segment.id}"
                            add_node(
                                node_id=hypothesis_id,
                                node_type="hypothesis",
                                label=self._truncate_label(reasoning_text, fallback="Hypothesis"),
                                data={
                                    "message_id": message.id,
                                    "segment_id": segment.id,
                                    "text": segment.text,
                                    "source_graphs": ["conversation", "transcript"],
                                },
                            )
                            add_edge(
                                source=latest_message_anchor_id,
                                target=hypothesis_id,
                                relation="hypothesizes",
                                data={"source_graphs": ["conversation", "transcript"]},
                            )
                            latest_message_anchor_id = hypothesis_id
                    continue

                if segment.kind not in {"tool_result", "output", "error"}:
                    continue

                observation_id = f"observation:{segment.id}"
                observation_data = self._conversation_observation_data(
                    message=message, segment=segment
                )
                add_node(
                    node_id=observation_id,
                    node_type="observation",
                    label=self._conversation_observation_label(segment, observation_data),
                    data=observation_data,
                )
                observation_source_id = (
                    tool_action_ids.get(segment.tool_call_id or "")
                    or latest_message_anchor_id
                    or message_action_id
                )
                add_edge(
                    source=observation_source_id,
                    target=observation_id,
                    relation=self._conversation_observation_relation(segment),
                    data={"source_graphs": ["conversation", "transcript"]},
                )
                latest_message_anchor_id = observation_id
                latest_message_observation_id = observation_id

            for index, reasoning_text in enumerate(
                self._conversation_reasoning_fragments(
                    message=message,
                    generation=generation,
                    transcript=transcript,
                ),
                start=1,
            ):
                hypothesis_key = reasoning_text.casefold()
                if hypothesis_key in seen_hypothesis_keys:
                    continue
                seen_hypothesis_keys.add(hypothesis_key)
                hypothesis_id = f"hypothesis:{message.id}:extra:{index}"
                add_node(
                    node_id=hypothesis_id,
                    node_type="hypothesis",
                    label=self._truncate_label(reasoning_text, fallback="Hypothesis"),
                    data={
                        "message_id": message.id,
                        "generation_id": generation.id if generation is not None else None,
                        "text": reasoning_text,
                        "source_graphs": ["conversation", "generation"],
                    },
                )
                add_edge(
                    source=message_action_id,
                    target=hypothesis_id,
                    relation="hypothesizes",
                    data={"source_graphs": ["conversation", "generation"]},
                )
                if latest_message_observation_id is not None:
                    add_edge(
                        source=latest_message_observation_id,
                        target=hypothesis_id,
                        relation="validates",
                        data={"source_graphs": ["conversation", "generation"]},
                    )
                latest_message_anchor_id = hypothesis_id

            latest_anchor_node_id = latest_message_anchor_id or message_action_id

        outcome_node_id = "outcome:conversation"
        latest_assistant_message = next(
            (
                message
                for message in reversed(ordered_messages)
                if message.role == MessageRole.ASSISTANT
            ),
            None,
        )
        latest_generation = (
            generation_by_assistant_message_id.get(latest_assistant_message.id)
            if latest_assistant_message is not None
            else None
        )
        latest_outcome_text = (
            latest_assistant_message.content.strip() if latest_assistant_message is not None else ""
        )
        outcome_status = (
            latest_generation.status.value
            if latest_generation is not None
            else (
                latest_assistant_message.status.value
                if latest_assistant_message is not None
                and latest_assistant_message.status is not None
                else "completed"
            )
        )
        add_node(
            node_id=outcome_node_id,
            node_type="outcome",
            label=self._truncate_label(
                self._extract_reasoning_text(latest_outcome_text)
                or latest_outcome_text
                or "Conversation outcome",
                fallback="Conversation outcome",
            ),
            data={
                "session_id": session.id,
                "message_id": (
                    latest_assistant_message.id if latest_assistant_message is not None else None
                ),
                "generation_id": latest_generation.id if latest_generation is not None else None,
                "status": outcome_status,
                "content": latest_outcome_text,
                "source_graphs": ["conversation", "generation"],
            },
        )
        if latest_anchor_node_id is not None and latest_anchor_node_id in nodes_by_id:
            add_edge(
                source=latest_anchor_node_id,
                target=outcome_node_id,
                relation="blocks" if outcome_status in {"failed", "cancelled"} else "confirms",
                data={"source_graphs": ["conversation"]},
            )

        return SessionGraphRead(
            session_id=session.id,
            workflow_run_id="",
            graph_type=GraphType.ATTACK,
            current_stage=None,
            nodes=sorted(
                nodes_by_id.values(),
                key=lambda node: (
                    self._NODE_TYPE_SORT_ORDER.get(node.node_type, 99),
                    node.label,
                    node.id,
                ),
            ),
            edges=sorted(
                edges_by_id.values(),
                key=lambda edge: (edge.source, edge.relation, edge.target, edge.id),
            ),
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

    @classmethod
    def _extract_reasoning_text(cls, value: object) -> str:
        if not isinstance(value, str):
            return ""
        without_tags = cls._THINK_TAG_RE.sub(" ", value)
        return re.sub(r"\s+", " ", without_tags).strip()

    @staticmethod
    def _truncate_label(value: str, *, fallback: str, max_length: int = 96) -> str:
        normalized = value.strip()
        if not normalized:
            return fallback
        if len(normalized) <= max_length:
            return normalized
        return f"{normalized[: max_length - 1].rstrip()}…"

    @classmethod
    def _conversation_action_label(
        cls, *, message: Message, generation: ChatGeneration | None
    ) -> str:
        if generation is not None:
            return cls._truncate_label(
                f"{generation.action.value} assistant response", fallback="Assistant action"
            )
        return cls._truncate_label("assistant response", fallback="Assistant action")

    @classmethod
    def _conversation_tool_action_label(cls, segment: object) -> str:
        command = cls._read_segment_command(segment)
        if command:
            return cls._truncate_label(command, fallback="Tool action")
        tool_name = getattr(segment, "tool_name", None)
        if isinstance(tool_name, str) and tool_name:
            return cls._truncate_label(tool_name, fallback="Tool action")
        return "Tool action"

    @staticmethod
    def _read_segment_command(segment: object) -> str | None:
        metadata = getattr(segment, "metadata_payload", {})
        if not isinstance(metadata, dict):
            return None
        command = metadata.get("command")
        if isinstance(command, str) and command.strip():
            return command.strip()
        arguments = metadata.get("arguments")
        if isinstance(arguments, dict):
            raw_command = arguments.get("command")
            if isinstance(raw_command, str) and raw_command.strip():
                return raw_command.strip()
        result = metadata.get("result")
        if isinstance(result, dict):
            raw_command = result.get("command")
            if isinstance(raw_command, str) and raw_command.strip():
                return raw_command.strip()
        text = getattr(segment, "text", None)
        if isinstance(text, str) and text.strip():
            return text.strip()
        return None

    @classmethod
    def _conversation_tool_node_type(cls, segment: object) -> str:
        tool_name = getattr(segment, "tool_name", None)
        normalized_tool_name = tool_name.casefold() if isinstance(tool_name, str) else ""
        if normalized_tool_name == "execute_kali_command":
            return "exploit"

        metadata = getattr(segment, "metadata_payload", {})
        skill_identifier: str | None = None
        if isinstance(metadata, dict):
            arguments = metadata.get("arguments")
            if isinstance(arguments, dict):
                raw_identifier = arguments.get("skill_name_or_id")
                if isinstance(raw_identifier, str) and raw_identifier.strip():
                    skill_identifier = raw_identifier.strip().casefold()
            if skill_identifier is None:
                result = metadata.get("result")
                if isinstance(result, dict):
                    skill = result.get("skill")
                    if isinstance(skill, dict):
                        raw_identifier = skill.get("directory_name") or skill.get("name")
                        if isinstance(raw_identifier, str) and raw_identifier.strip():
                            skill_identifier = raw_identifier.strip().casefold()

        if normalized_tool_name == "execute_skill" and skill_identifier is not None:
            offensive_tokens = (
                "adscan",
                "adpwn",
                "bypass",
                "movement",
                "privesc",
                "persistence",
                "tunnel",
                "ctf-web",
                "ctf-pwn",
                "wooyun",
                "c2",
                "evasion",
            )
            if any(token in skill_identifier for token in offensive_tokens):
                return "exploit"

        return "action"

    @classmethod
    def _conversation_observation_data(
        cls, *, message: Message, segment: object
    ) -> dict[str, object]:
        metadata = getattr(segment, "metadata_payload", {})
        result = metadata.get("result") if isinstance(metadata, dict) else None
        payload: dict[str, object] = {
            "message_id": message.id,
            "segment_id": getattr(segment, "id", None),
            "tool_name": getattr(segment, "tool_name", None),
            "tool_call_id": getattr(segment, "tool_call_id", None),
            "status": getattr(segment, "status", None),
            "text": getattr(segment, "text", None),
            "source_graphs": ["conversation", "transcript"],
        }
        if isinstance(metadata, dict):
            payload.update(metadata)
        if isinstance(result, dict):
            payload.update(result)
        return payload

    @classmethod
    def _conversation_observation_label(
        cls, segment: object, observation_data: dict[str, object]
    ) -> str:
        for candidate in (
            observation_data.get("command"),
            observation_data.get("stdout"),
            getattr(segment, "text", None),
            getattr(segment, "tool_name", None),
        ):
            normalized = cls._extract_reasoning_text(candidate)
            if normalized:
                return cls._truncate_label(normalized, fallback="Observation")
        return "Observation"

    @staticmethod
    def _conversation_observation_relation(segment: object) -> str:
        segment_kind = getattr(segment, "kind", None)
        segment_status = str(getattr(segment, "status", "") or "")
        if segment_kind == "error" or segment_status in {"failed", "cancelled"}:
            return "blocks"
        if segment_kind == "output":
            return "observes"
        return "discovers"

    @classmethod
    def _conversation_reasoning_fragments(
        cls,
        *,
        message: Message,
        generation: ChatGeneration | None,
        transcript: Sequence[object],
    ) -> list[str]:
        existing = {
            cls._extract_reasoning_text(getattr(segment, "text", None)).casefold()
            for segment in transcript
            if getattr(segment, "kind", None) == "reasoning"
            and cls._extract_reasoning_text(getattr(segment, "text", None))
        }
        fragments: list[str] = []
        candidates: list[object] = []
        if generation is not None and generation.reasoning_summary:
            candidates.append(generation.reasoning_summary)
            candidates.extend(generation.reasoning_trace_json)
        raw_trace = message.metadata_json.get("trace")
        if isinstance(raw_trace, list):
            candidates.extend(raw_trace)
        for candidate in candidates:
            text = ""
            if isinstance(candidate, str):
                text = cls._extract_reasoning_text(candidate)
            elif isinstance(candidate, dict):
                for key in ("summary", "safe_summary", "message", "text"):
                    value = candidate.get(key)
                    text = cls._extract_reasoning_text(value)
                    if text:
                        break
            if not text or text.casefold() in existing:
                continue
            existing.add(text.casefold())
            fragments.append(text)
        return fragments
