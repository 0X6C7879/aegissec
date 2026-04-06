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
    TaskNodeType,
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


class ExecutionGraphBuilder:
    _THINK_TAG_RE = re.compile(r"</?think\b[^>]*>", re.IGNORECASE)
    _NODE_TYPE_SORT_ORDER: dict[str, int] = {
        "root": 0,
        "task": 1,
        "action": 2,
        "outcome": 3,
    }
    _EXECUTION_SOURCE_GRAPHS = frozenset(
        {"task", "workflow", "conversation", "generation", "transcript"}
    )
    _PRESERVED_ATTACK_STATUS_VALUES = frozenset({"in_progress", "blocked", "failed"})

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
        task_by_id = {task.id: task for task in ordered_tasks}
        action_node_ids_by_trace: dict[str, str] = {}
        action_node_ids_by_merge_key: dict[str, str] = {}
        latest_action_by_task_id: dict[str, str] = {}
        task_stage_to_id: dict[str, str] = {}
        synthetic_action_index = 0
        for task in ordered_tasks:
            stage_key = task.metadata_json.get("stage_key")
            if isinstance(stage_key, str) and task.node_type is TaskNodeType.STAGE:
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
            anchored_data = with_anchor_data(data)
            existing = nodes_by_id.get(node_id)
            if existing is not None:
                merged_type = self._prefer_attack_node_type(existing.node_type, node_type)
                nodes_by_id[node_id] = SessionGraphNodeRead(
                    id=node_id,
                    graph_type=GraphType.ATTACK,
                    node_type=merged_type,
                    label=self._choose_node_label(existing.label, label),
                    data=self._merge_attack_node_data(existing.data, anchored_data),
                )
                return
            nodes_by_id[node_id] = SessionGraphNodeRead(
                id=node_id,
                graph_type=GraphType.ATTACK,
                node_type=node_type,
                label=label,
                data=anchored_data,
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
        root_node_id: str | None = None
        if isinstance(goal, str) and goal.strip():
            root_node_id = f"root:{run.id}"
            add_node(
                node_id=root_node_id,
                node_type="root",
                label=goal.strip(),
                data={
                    "goal": goal.strip(),
                    "status": run.status.value,
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
                node_type="task",
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
                    "thought": task.metadata_json.get("thought"),
                    "observation_summary": task.metadata_json.get("summary"),
                    "related_findings": [],
                    "related_hypotheses": [],
                    "source_graphs": ["task"],
                },
            )

        def add_execution_action(
            *,
            payload: dict[str, object],
            fallback_label: str,
            source_graphs: list[str],
            relation_hint: str | None = None,
        ) -> str:
            nonlocal synthetic_action_index
            task_anchor_id = self._resolve_task_anchor_id(
                task_id=payload.get("task_id") or payload.get("task_node_id"),
                task_name=payload.get("task_name") or payload.get("task"),
                task_ids=task_ids,
                task_name_to_id=task_name_to_id,
            )
            synthetic_action_index += 1
            action_id = self._resolve_execution_action_id(
                payload=payload,
                task_anchor_id=task_anchor_id,
                fallback=f"action:{run.id}:{synthetic_action_index}",
                trace_to_node_id=action_node_ids_by_trace,
                action_key_to_id=action_node_ids_by_merge_key,
            )
            task_anchor = task_by_id.get(task_anchor_id) if task_anchor_id is not None else None
            action_data = self._execution_payload(payload, source_graphs=source_graphs)
            action_data["action_id"] = action_id
            action_data["task_id"] = task_anchor_id or action_data.get("task_id")
            action_data["task_name"] = (
                task_anchor.name if task_anchor is not None else action_data.get("task_name")
            )
            action_data["stage_key"] = (
                task_anchor.metadata_json.get("stage_key")
                if task_anchor is not None
                else payload.get("stage_key")
            )
            action_data["blocked_reason"] = payload.get("blocked_reason") or payload.get(
                "last_error"
            )
            action_data["merged_from"] = self._merge_source_graphs(
                action_data.get("merged_from"), source_graphs
            )
            add_node(
                node_id=action_id,
                node_type="action",
                label=self._execution_node_label(fallback_label, payload, task_anchor),
                data=action_data,
            )
            trace_id = payload.get("trace_id") or payload.get("id")
            if isinstance(trace_id, str) and trace_id:
                action_node_ids_by_trace[trace_id] = action_id
            merge_key = self._execution_merge_key(payload=payload, task_anchor_id=task_anchor_id)
            if merge_key is not None:
                action_node_ids_by_merge_key[merge_key] = action_id
            if task_anchor_id is not None:
                add_edge(
                    source=task_anchor_id,
                    target=action_id,
                    relation=relation_hint
                    or self._execution_relation(str(action_data.get("status") or "")),
                    data={"source_graphs": self._merge_source_graphs(["task"], source_graphs)},
                )
                previous_action_id = latest_action_by_task_id.get(task_anchor_id)
                if previous_action_id is not None and previous_action_id != action_id:
                    add_edge(
                        source=previous_action_id,
                        target=action_id,
                        relation="precedes",
                        data={
                            "source_graphs": self._merge_source_graphs(["workflow"], source_graphs)
                        },
                    )
                latest_action_by_task_id[task_anchor_id] = action_id
            return action_id

        if root_node_id is not None:
            root_tasks = [task for task in ordered_tasks if not self._task_dependencies(task)]
            for task in root_tasks:
                add_edge(
                    source=root_node_id,
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

        if evidence_nodes:
            for node in sorted(
                evidence_nodes, key=lambda item: (item.stable_key, item.created_at, item.id)
            ):
                payload = dict(node.payload_json)
                add_execution_action(
                    payload=payload,
                    fallback_label=node.label,
                    source_graphs=["evidence"],
                )
        execution_records = run.state_json.get("execution_records", [])
        if isinstance(execution_records, list):
            for index, record in enumerate(execution_records):
                if not isinstance(record, dict):
                    continue
                add_execution_action(
                    payload=dict(record),
                    fallback_label=str(
                        record.get("command_or_action")
                        or record.get("request_summary")
                        or record.get("task_name")
                        or f"Action {index + 1}"
                    ),
                    source_graphs=["workflow"],
                )

        hypothesis_updates = run.state_json.get("hypothesis_updates", [])
        if isinstance(hypothesis_updates, list):
            for index, update in enumerate(hypothesis_updates):
                if not isinstance(update, dict):
                    continue
                result = str(update.get("result") or "unknown")
                add_execution_action(
                    payload={
                        **update,
                        "task_name": update.get("task"),
                        "status": update.get("status")
                        or ("blocked" if result == "blocked" else None),
                        "thought": update.get("summary") or update.get("kind"),
                        "related_hypotheses": [
                            {
                                "kind": update.get("kind"),
                                "summary": update.get("summary"),
                                "result": update.get("result"),
                                "status": update.get("status"),
                                "trace_id": update.get("trace_id"),
                                "task": update.get("task"),
                            }
                        ],
                    },
                    fallback_label=str(
                        update.get("kind") or update.get("summary") or f"Hypothesis {index + 1}"
                    ),
                    source_graphs=["workflow"],
                    relation_hint="attempts",
                )

        if causal_nodes:
            for node in sorted(
                causal_nodes, key=lambda item: (item.stable_key, item.created_at, item.id)
            ):
                payload = dict(node.payload_json)
                add_execution_action(
                    payload={
                        **payload,
                        "task_name": payload.get("task"),
                        "related_findings": [self._finding_summary(payload, node.label)],
                    },
                    fallback_label=node.label,
                    source_graphs=["causal"],
                    relation_hint="confirms",
                )
        else:
            findings = run.state_json.get("findings", [])
            if isinstance(findings, list):
                normalized_findings = [finding for finding in findings if isinstance(finding, dict)]
                for index, finding in enumerate(normalized_findings):
                    add_execution_action(
                        payload={
                            **finding,
                            "task_name": finding.get("task"),
                            "related_findings": [
                                self._finding_summary(
                                    finding,
                                    str(
                                        finding.get("title")
                                        or finding.get("id")
                                        or f"Finding {index + 1}"
                                    ),
                                )
                            ],
                        },
                        fallback_label=str(
                            finding.get("title") or finding.get("id") or f"Finding {index + 1}"
                        ),
                        source_graphs=["workflow"],
                        relation_hint="confirms",
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
                "related_findings": [],
                "related_hypotheses": [],
                "source_graphs": ["workflow"],
            },
        )
        leaf_node_ids = self._execution_leaf_node_ids(
            nodes_by_id=nodes_by_id, edges_by_id=edges_by_id
        )
        if not leaf_node_ids:
            anchor_task_id = self._current_stage_task_id(
                ordered_tasks,
                current_stage=run.current_stage,
                task_stage_to_id=task_stage_to_id,
            )
            if anchor_task_id is not None:
                leaf_node_ids = {anchor_task_id}
        if root_node_id is not None and not leaf_node_ids:
            leaf_node_ids = {root_node_id}
        for node_id in sorted(leaf_node_ids):
            if node_id == outcome_node_id:
                continue
            node_status = str(nodes_by_id[node_id].data.get("status") or "")
            leaf_relation = (
                self._execution_relation(node_status)
                if outcome_relation == "attempts"
                else outcome_relation
            )
            add_edge(
                source=node_id,
                target=outcome_node_id,
                relation=leaf_relation,
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
        normalized_nodes_by_id = {
            node_id: normalized
            for node_id, node in nodes_by_id.items()
            if (normalized := self._normalize_attack_view_node(node)) is not None
        }
        outgoing_edges_by_node: dict[str, list[SessionGraphEdgeRead]] = {}
        for edge in edges_by_id.values():
            outgoing_edges_by_node.setdefault(edge.source, []).append(edge)

        removable_node_ids = {
            node_id
            for node_id, node in normalized_nodes_by_id.items()
            if self._should_remove_low_signal_execution_node(
                node=node,
                normalized_nodes_by_id=normalized_nodes_by_id,
                outgoing_edges_by_node=outgoing_edges_by_node,
                execution_node_count=sum(
                    1
                    for candidate in normalized_nodes_by_id.values()
                    if candidate.node_type in {"task", "action"}
                ),
            )
        }
        if removable_node_ids:
            normalized_nodes_by_id = {
                node_id: node
                for node_id, node in normalized_nodes_by_id.items()
                if node_id not in removable_node_ids
            }

        pruned_edges_by_id: dict[str, SessionGraphEdgeRead] = {}
        for node_id in normalized_nodes_by_id:
            self._collect_visible_attack_edges(
                source_id=node_id,
                normalized_nodes_by_id=normalized_nodes_by_id,
                outgoing_edges_by_node=outgoing_edges_by_node,
                pruned_edges_by_id=pruned_edges_by_id,
            )

        pruned_edges_by_id = {
            edge_id: edge
            for edge_id, edge in pruned_edges_by_id.items()
            if edge.source in normalized_nodes_by_id and edge.target in normalized_nodes_by_id
        }
        return normalized_nodes_by_id, pruned_edges_by_id

    def _collect_visible_attack_edges(
        self,
        *,
        source_id: str,
        normalized_nodes_by_id: dict[str, SessionGraphNodeRead],
        outgoing_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
        pruned_edges_by_id: dict[str, SessionGraphEdgeRead],
    ) -> None:
        pending: list[tuple[str, str, dict[str, object]]] = []
        seen_states: set[tuple[str, str, str]] = set()
        for edge in outgoing_edges_by_node.get(source_id, []):
            pending.append((edge.target, edge.relation, dict(edge.data)))

        while pending:
            target_id, relation, data = pending.pop()
            state_key = (source_id, target_id, relation)
            if state_key in seen_states:
                continue
            seen_states.add(state_key)
            if target_id == source_id:
                continue
            if target_id in normalized_nodes_by_id:
                edge_id = f"attack:{source_id}:{relation}:{target_id}"
                existing = pruned_edges_by_id.get(edge_id)
                edge_data = self._dedupe_edge_data(data)
                if existing is None:
                    pruned_edges_by_id[edge_id] = SessionGraphEdgeRead(
                        id=edge_id,
                        graph_type=GraphType.ATTACK,
                        source=source_id,
                        target=target_id,
                        relation=relation,
                        data=edge_data,
                    )
                else:
                    pruned_edges_by_id[edge_id] = SessionGraphEdgeRead(
                        id=edge_id,
                        graph_type=GraphType.ATTACK,
                        source=source_id,
                        target=target_id,
                        relation=relation,
                        data=self._merge_attack_edge_dicts(existing.data, edge_data),
                    )
                continue
            for outgoing_edge in outgoing_edges_by_node.get(target_id, []):
                pending.append(
                    (
                        outgoing_edge.target,
                        self._bridge_attack_edge_relation(
                            incoming_relation=relation,
                            outgoing_relation=outgoing_edge.relation,
                        ),
                        self._merge_attack_edge_dicts(data, outgoing_edge.data),
                    )
                )

    def _node_is_active(self, node: SessionGraphNodeRead) -> bool:
        status = node.data.get("status")
        return bool(node.data.get("current") or node.data.get("active") or status == "in_progress")

    def _node_has_preserved_status(self, node: SessionGraphNodeRead) -> bool:
        status = node.data.get("status")
        return self._node_is_active(node) or status in self._PRESERVED_ATTACK_STATUS_VALUES

    def _should_remove_low_signal_execution_node(
        self,
        *,
        node: SessionGraphNodeRead,
        normalized_nodes_by_id: dict[str, SessionGraphNodeRead],
        outgoing_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
        execution_node_count: int,
    ) -> bool:
        if node.node_type not in {"task", "action"}:
            return False
        if execution_node_count <= 1 and self._node_has_runtime_provenance(node):
            return False
        if self._node_has_preserved_status(node):
            return False
        if node.data.get("current"):
            return False
        if isinstance(node.data.get("related_findings"), list) and node.data.get(
            "related_findings"
        ):
            return False
        if self._node_has_execution_signal(node):
            return False
        for edge in outgoing_edges_by_node.get(node.id, []):
            if edge.target in normalized_nodes_by_id and not edge.target.startswith("outcome:"):
                return False
        return True

    def _node_has_execution_signal(self, node: SessionGraphNodeRead) -> bool:
        execution_fields = (
            "tool_name",
            "command",
            "request_summary",
            "arguments",
            "result",
            "observation",
            "response_excerpt",
            "stdout",
            "stderr",
            "exit_code",
        )
        for field in execution_fields:
            value = node.data.get(field)
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, list) and not value:
                continue
            return True
        return False

    def _node_has_runtime_provenance(self, node: SessionGraphNodeRead) -> bool:
        source_graphs = node.data.get("source_graphs")
        if not isinstance(source_graphs, list):
            return False
        return any(
            isinstance(source, str)
            and source in {"workflow", "evidence", "conversation", "transcript", "generation"}
            for source in source_graphs
        ) or isinstance(node.data.get("trace_id"), str)

    def _bridge_attack_edge_relation(
        self, *, incoming_relation: str, outgoing_relation: str
    ) -> str:
        if outgoing_relation != "attempts":
            return outgoing_relation
        return incoming_relation

    def _merge_attack_edge_dicts(
        self,
        first: dict[str, object],
        second: dict[str, object],
    ) -> dict[str, object]:
        merged: dict[str, object] = {**dict(first), **dict(second)}
        merged["source_graphs"] = self._merge_source_graphs(
            first.get("source_graphs"), second.get("source_graphs")
        )
        return self._dedupe_edge_data(merged)

    def _dedupe_edge_data(self, data: dict[str, object]) -> dict[str, object]:
        deduped = dict(data)
        deduped["source_graphs"] = self._merge_source_graphs(deduped.get("source_graphs"))
        return deduped

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
            existing = nodes_by_id.get(node_id)
            if existing is not None:
                nodes_by_id[node_id] = SessionGraphNodeRead(
                    id=node_id,
                    graph_type=GraphType.ATTACK,
                    node_type=self._prefer_attack_node_type(existing.node_type, node_type),
                    label=self._choose_node_label(existing.label, label),
                    data=self._merge_attack_node_data(existing.data, data),
                )
                return
            nodes_by_id[node_id] = SessionGraphNodeRead(
                id=node_id,
                graph_type=GraphType.ATTACK,
                node_type=node_type,
                label=label,
                data=self._merge_attack_node_data({}, data),
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

        goal_node_id = "root:conversation"
        if goal_text:
            add_node(
                node_id=goal_node_id,
                node_type="root",
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
                    "response_excerpt": self._extract_reasoning_text(message.content)
                    or message.content,
                    "related_hypotheses": [],
                    "related_findings": [],
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
                        node_type="action",
                        label=self._conversation_tool_action_label(segment),
                        data={
                            "message_id": message.id,
                            "tool_call_id": segment.tool_call_id,
                            "tool_name": segment.tool_name,
                            "arguments": dict(segment.metadata_payload),
                            "command": self._read_segment_command(segment),
                            "related_hypotheses": [],
                            "related_findings": [],
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
                            add_node(
                                node_id=latest_message_anchor_id,
                                node_type="action",
                                label=nodes_by_id[latest_message_anchor_id].label,
                                data={
                                    "thought": reasoning_text,
                                    "related_hypotheses": [
                                        {
                                            "message_id": message.id,
                                            "segment_id": segment.id,
                                            "summary": reasoning_text,
                                            "text": segment.text,
                                        }
                                    ],
                                    "source_graphs": ["conversation", "transcript"],
                                },
                            )
                    continue

                if segment.kind not in {"tool_result", "output", "error"}:
                    continue

                observation_data = self._conversation_observation_data(
                    message=message, segment=segment
                )
                add_node(
                    node_id=(
                        tool_action_ids.get(segment.tool_call_id or "")
                        or latest_message_anchor_id
                        or message_action_id
                    ),
                    node_type="action",
                    label=self._conversation_observation_label(segment, observation_data),
                    data=self._execution_payload(
                        observation_data, source_graphs=["conversation", "transcript"]
                    ),
                )
                latest_message_observation_id = (
                    tool_action_ids.get(segment.tool_call_id or "")
                    or latest_message_anchor_id
                    or message_action_id
                )
                latest_message_anchor_id = latest_message_observation_id

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
                add_node(
                    node_id=message_action_id,
                    node_type="action",
                    label=nodes_by_id[message_action_id].label,
                    data={
                        "thought": reasoning_text,
                        "related_hypotheses": [
                            {
                                "message_id": message.id,
                                "generation_id": generation.id if generation is not None else None,
                                "summary": reasoning_text,
                                "index": index,
                            }
                        ],
                        "source_graphs": ["conversation", "generation"],
                    },
                )
                latest_message_anchor_id = latest_message_observation_id or message_action_id

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
        return "task" if task.node_type is TaskNodeType.STAGE else "action"

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

    def _resolve_execution_node_id(
        self,
        *,
        trace_id: object,
        task_id: object,
        task_name: object,
        task_ids: set[str],
        task_name_to_id: dict[str, str],
        fallback: str,
        trace_to_node_id: dict[str, str] | None = None,
    ) -> str:
        if isinstance(trace_id, str) and trace_id:
            if trace_to_node_id is not None and trace_id in trace_to_node_id:
                return trace_to_node_id[trace_id]
            if isinstance(task_id, str) and task_id in task_ids:
                return task_id
            if isinstance(task_name, str) and task_name in task_name_to_id:
                return task_name_to_id[task_name]
            return trace_id
        if isinstance(task_id, str) and task_id in task_ids:
            return task_id
        if isinstance(task_name, str) and task_name in task_name_to_id:
            return task_name_to_id[task_name]
        return fallback

    def _execution_node_type_for_id(
        self, node_id: str, existing: SessionGraphNodeRead | None
    ) -> str:
        if existing is not None:
            return existing.node_type
        if node_id.startswith("root:"):
            return "root"
        if node_id.startswith("outcome:"):
            return "outcome"
        return "action"

    def _execution_node_label(
        self,
        fallback_label: str,
        payload: dict[str, object],
        task_anchor: TaskNode | None = None,
    ) -> str:
        return self._truncate_label(
            self._pick_first_str(
                payload.get("command"),
                payload.get("command_or_action"),
                payload.get("request_summary"),
                payload.get("tool_name"),
                payload.get("summary"),
                payload.get("observation_summary"),
                payload.get("stdout"),
                task_anchor.metadata_json.get("title") if task_anchor is not None else None,
                fallback_label,
            )
            or fallback_label,
            fallback=fallback_label or "Action",
        )

    def _execution_payload(
        self,
        payload: dict[str, object],
        *,
        source_graphs: list[str],
    ) -> dict[str, object]:
        summary = self._pick_first_str(
            payload.get("observation_summary"),
            payload.get("summary"),
            payload.get("result_summary"),
            payload.get("observation"),
            payload.get("stdout"),
            payload.get("text"),
        )
        return {
            "task_id": payload.get("task_id") or payload.get("task_node_id"),
            "task_name": payload.get("task_name") or payload.get("task"),
            "status": payload.get("status"),
            "current": payload.get("current") or payload.get("active"),
            "thought": payload.get("thought"),
            "tool_name": payload.get("tool_name"),
            "command": payload.get("command") or payload.get("command_or_action"),
            "request_summary": payload.get("request_summary") or payload.get("intent"),
            "arguments": payload.get("arguments"),
            "result": payload.get("result") if isinstance(payload.get("result"), dict) else None,
            "observation": payload.get("observation") or payload.get("text"),
            "observation_summary": summary,
            "response_excerpt": payload.get("response_excerpt") or payload.get("text"),
            "stdout": payload.get("stdout"),
            "stderr": payload.get("stderr"),
            "exit_code": payload.get("exit_code"),
            "trace_id": payload.get("trace_id") or payload.get("id"),
            "updated_at": payload.get("updated_at"),
            "completed_at": payload.get("completed_at") or payload.get("ended_at"),
            "blocked_reason": payload.get("blocked_reason") or payload.get("last_error"),
            "source_message_id": payload.get("source_message_id"),
            "related_findings": [],
            "related_hypotheses": [],
            "source_graphs": source_graphs,
            "merged_from": list(source_graphs),
        }

    def _resolve_task_anchor_id(
        self,
        *,
        task_id: object,
        task_name: object,
        task_ids: set[str],
        task_name_to_id: dict[str, str],
    ) -> str | None:
        if isinstance(task_id, str) and task_id in task_ids:
            return task_id
        if isinstance(task_name, str) and task_name in task_name_to_id:
            return task_name_to_id[task_name]
        return None

    def _execution_merge_key(
        self,
        *,
        payload: dict[str, object],
        task_anchor_id: str | None,
    ) -> str | None:
        trace_id = payload.get("trace_id") or payload.get("id")
        if isinstance(trace_id, str) and trace_id:
            return f"trace:{trace_id}"
        if task_anchor_id is None:
            return None
        tool_name = self._pick_first_str(payload.get("tool_name"), payload.get("tool"))
        command_summary = self._pick_first_str(
            payload.get("command"),
            payload.get("command_or_action"),
            payload.get("request_summary"),
        )
        if tool_name and command_summary:
            return f"task-tool:{task_anchor_id}:{tool_name.casefold()}:{self._normalize_merge_label(command_summary)}"
        label = self._pick_first_str(
            payload.get("request_summary"),
            payload.get("summary"),
            payload.get("observation_summary"),
            payload.get("stdout"),
        )
        if label:
            return f"task-label:{task_anchor_id}:{self._normalize_merge_label(label)}"
        return f"task:{task_anchor_id}"

    def _resolve_execution_action_id(
        self,
        *,
        payload: dict[str, object],
        task_anchor_id: str | None,
        fallback: str,
        trace_to_node_id: dict[str, str],
        action_key_to_id: dict[str, str],
    ) -> str:
        trace_id = payload.get("trace_id") or payload.get("id")
        if isinstance(trace_id, str) and trace_id in trace_to_node_id:
            return trace_to_node_id[trace_id]
        merge_key = self._execution_merge_key(payload=payload, task_anchor_id=task_anchor_id)
        if merge_key is not None and merge_key in action_key_to_id:
            return action_key_to_id[merge_key]
        if isinstance(trace_id, str) and trace_id:
            return f"action:{trace_id}"
        if merge_key is not None:
            return f"action:{self._normalize_merge_label(merge_key)}"
        return fallback

    @staticmethod
    def _normalize_merge_label(value: str) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
        return normalized or "step"

    def _finding_summary(self, finding: dict[str, object], label: str) -> dict[str, object]:
        return {
            "id": finding.get("id"),
            "title": finding.get("title") or label,
            "label": label,
            "kind": finding.get("kind"),
            "summary": finding.get("summary"),
            "confidence": finding.get("confidence"),
            "trace_id": finding.get("trace_id"),
            "task": finding.get("task"),
            "supports": finding.get("supports"),
            "contradicts": finding.get("contradicts"),
            "validates": finding.get("validates"),
            "causes": finding.get("causes"),
        }

    def _execution_leaf_node_ids(
        self,
        *,
        nodes_by_id: dict[str, SessionGraphNodeRead],
        edges_by_id: dict[str, SessionGraphEdgeRead],
    ) -> set[str]:
        outgoing_counts: dict[str, int] = {}
        for edge in edges_by_id.values():
            if edge.target.startswith("outcome:"):
                continue
            outgoing_counts[edge.source] = outgoing_counts.get(edge.source, 0) + 1
        return {
            node.id
            for node in nodes_by_id.values()
            if node.node_type in {"task", "action"} and outgoing_counts.get(node.id, 0) == 0
        }

    def _normalize_attack_view_node(
        self, node: SessionGraphNodeRead
    ) -> SessionGraphNodeRead | None:
        normalized_type: str | None
        if node.node_type in {"root", "task", "action", "outcome"}:
            normalized_type = node.node_type
        elif node.node_type == "goal":
            normalized_type = "root"
        elif node.node_type in {"observation", "hypothesis"}:
            normalized_type = None
        else:
            source_graphs = set(self._merge_source_graphs(node.data.get("source_graphs")))
            normalized_type = "action" if source_graphs & self._EXECUTION_SOURCE_GRAPHS else None
        if normalized_type is None:
            return None
        return SessionGraphNodeRead(
            id=node.id,
            graph_type=node.graph_type,
            node_type=normalized_type,
            label=node.label,
            data=self._merge_attack_node_data({}, node.data),
        )

    def _prefer_attack_node_type(self, current_type: str, incoming_type: str) -> str:
        if current_type == incoming_type:
            return current_type
        return (
            current_type
            if self._NODE_TYPE_SORT_ORDER.get(current_type, 99)
            <= self._NODE_TYPE_SORT_ORDER.get(incoming_type, 99)
            else incoming_type
        )

    def _choose_node_label(self, current_label: str, incoming_label: str) -> str:
        if not current_label.strip():
            return incoming_label
        if current_label.startswith("Workflow ") and incoming_label.strip():
            return incoming_label
        if len(incoming_label.strip()) > len(current_label.strip()) and incoming_label.strip():
            return incoming_label
        return current_label

    def _merge_attack_node_data(
        self,
        current: dict[str, object],
        incoming: dict[str, object],
    ) -> dict[str, object]:
        merged: dict[str, object] = {**dict(current)}
        incoming_dict = dict(incoming)
        for key, value in incoming_dict.items():
            if key in {"related_findings", "related_hypotheses", "source_graphs"}:
                continue
            if value is None:
                continue
            existing = merged.get(key)
            if isinstance(value, str):
                if key in {
                    "observation_summary",
                    "response_excerpt",
                    "stdout",
                    "stderr",
                    "trace_id",
                }:
                    if value.strip():
                        merged[key] = value
                    continue
                if value.strip() and (not isinstance(existing, str) or not existing.strip()):
                    merged[key] = value
                elif key == "status" and value in self._PRESERVED_ATTACK_STATUS_VALUES:
                    merged[key] = value
            elif isinstance(value, list):
                if value and not isinstance(existing, list):
                    merged[key] = list(value)
            elif isinstance(value, dict):
                if value and not isinstance(existing, dict):
                    merged[key] = dict(value)
            else:
                if existing is None:
                    merged[key] = value
                elif key == "current" and value:
                    merged[key] = value
        merged["source_graphs"] = self._merge_source_graphs(
            current.get("source_graphs"), incoming_dict.get("source_graphs")
        )
        merged["related_findings"] = self._merge_related_items(
            current.get("related_findings"),
            incoming_dict.get("related_findings"),
            ("id", "title", "label"),
        )
        merged["related_hypotheses"] = self._merge_related_items(
            current.get("related_hypotheses"),
            incoming_dict.get("related_hypotheses"),
            ("trace_id", "summary", "kind", "index"),
        )
        return merged

    def _merge_source_graphs(self, *values: object) -> list[str]:
        merged: list[str] = []
        for value in values:
            if not isinstance(value, list):
                continue
            for item in value:
                if isinstance(item, str) and item not in merged:
                    merged.append(item)
        return merged

    def _merge_related_items(
        self,
        current: object,
        incoming: object,
        key_fields: tuple[str, ...],
    ) -> list[dict[str, object]]:
        merged: list[dict[str, object]] = []
        seen: set[tuple[object, ...]] = set()
        for raw_items in (current, incoming):
            if not isinstance(raw_items, list):
                continue
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                key = tuple(item.get(field) for field in key_fields)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(dict(item))
        return merged

    @staticmethod
    def _pick_first_str(*values: object) -> str | None:
        for value in values:
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

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


class AttackGraphBuilder(ExecutionGraphBuilder):
    pass
