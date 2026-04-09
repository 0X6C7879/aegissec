from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

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


@dataclass(slots=True)
class ExecutionCandidate:
    candidate_id: str
    fallback_label: str
    payload: dict[str, object]
    source_graphs: list[str]
    relation_hint: str | None
    task_id: str | None
    order: int
    family_key: str


@dataclass(slots=True)
class ExecutionMilestone:
    node_id: str
    label: str
    data: dict[str, object]
    task_id: str | None
    family_key: str
    first_order: int
    last_order: int
    relation_hint: str | None
    score: int
    reasons: tuple[str, ...]


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
    _URL_RE = re.compile(r"https?://[^\s\"'>]+", re.IGNORECASE)
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
    _PRUNE_EXECUTION_THRESHOLD = 8
    _STAGE_RANKS: dict[str, int] = {
        "reconnaissance": 0,
        "validation": 1,
        "exploit": 2,
        "pivot": 3,
        "outcome": 4,
        "unknown": 0,
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
        del causal_edges
        ordered_tasks = sorted(tasks, key=lambda task: (task.sequence, task.created_at, task.id))
        nodes_by_id: dict[str, SessionGraphNodeRead] = {}
        edges_by_id: dict[str, SessionGraphEdgeRead] = {}
        task_ids = {task.id for task in ordered_tasks}
        task_name_to_id = {task.name: task.id for task in ordered_tasks}
        task_by_id = {task.id: task for task in ordered_tasks}
        task_stage_to_id: dict[str, str] = {}
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

        candidate_actions = self._build_candidate_execution_nodes(
            run=run,
            evidence_nodes=evidence_nodes or [],
            causal_nodes=causal_nodes,
            task_ids=task_ids,
            task_name_to_id=task_name_to_id,
            task_by_id=task_by_id,
        )
        milestone_actions = self._select_milestone_execution_nodes(
            candidates=candidate_actions,
            run_status=run.status,
            current_stage=run.current_stage,
            ordered_tasks=ordered_tasks,
        )
        if root_node_id is not None and milestone_actions:
            add_node(
                node_id=root_node_id,
                node_type="root",
                label=nodes_by_id[root_node_id].label,
                data={"best_path_summary": milestone_actions[-1].label},
            )

        milestones_by_task_id: dict[str | None, list[ExecutionMilestone]] = {}
        for milestone in milestone_actions:
            add_node(
                node_id=milestone.node_id,
                node_type="action",
                label=milestone.label,
                data=milestone.data,
            )
            milestones_by_task_id.setdefault(milestone.task_id, []).append(milestone)

        for task in ordered_tasks:
            task_milestones = milestones_by_task_id.get(task.id, [])
            if not task_milestones:
                continue
            latest_milestone = max(
                task_milestones,
                key=lambda item: (item.score, item.last_order, item.node_id),
            )
            add_node(
                node_id=task.id,
                node_type="task",
                label=nodes_by_id[task.id].label,
                data={
                    "current_action_summary": latest_milestone.label,
                    "key_observation_summary": self._pick_first_str(
                        latest_milestone.data.get("best_observation_summary"),
                        latest_milestone.data.get("observation_summary"),
                    ),
                    "blocker": (
                        latest_milestone.data.get("blocked_reason")
                        if str(latest_milestone.data.get("status") or "") in {"blocked", "failed"}
                        else None
                    ),
                    "next_step": (
                        latest_milestone.label
                        if task.status in {TaskNodeStatus.IN_PROGRESS, TaskNodeStatus.READY}
                        else None
                    ),
                },
            )

        for task_id, milestones in milestones_by_task_id.items():
            ordered_milestones = sorted(
                milestones, key=lambda item: (item.first_order, item.last_order, item.node_id)
            )
            if not ordered_milestones:
                continue

            first_milestone = ordered_milestones[0]
            relation = first_milestone.relation_hint or self._execution_relation(
                str(first_milestone.data.get("status") or "")
            )
            if task_id is not None and task_id in nodes_by_id:
                add_edge(
                    source=task_id,
                    target=first_milestone.node_id,
                    relation=relation,
                    data={"source_graphs": first_milestone.data.get("source_graphs", [])},
                )
            elif root_node_id is not None:
                add_edge(
                    source=root_node_id,
                    target=first_milestone.node_id,
                    relation=relation,
                    data={"source_graphs": first_milestone.data.get("source_graphs", [])},
                )

            for previous_milestone, current_milestone in zip(
                ordered_milestones, ordered_milestones[1:]
            ):
                add_edge(
                    source=previous_milestone.node_id,
                    target=current_milestone.node_id,
                    relation="precedes",
                    data={
                        "source_graphs": self._merge_source_graphs(
                            previous_milestone.data.get("source_graphs"),
                            current_milestone.data.get("source_graphs"),
                            ["workflow"],
                        )
                    },
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
                "supporting_actions": [milestone.label for milestone in milestone_actions[:3]],
                "source_graphs": ["workflow"],
            },
        )
        outcome_anchor_id = self._pick_outcome_anchor_id(
            milestones=milestone_actions,
            run_status=run.status,
            current_stage_task_id=self._current_stage_task_id(
                ordered_tasks,
                current_stage=run.current_stage,
                task_stage_to_id=task_stage_to_id,
            ),
            root_node_id=root_node_id,
        )
        if outcome_anchor_id is not None:
            anchor_status = str(nodes_by_id[outcome_anchor_id].data.get("status") or "")
            leaf_relation = (
                self._execution_relation(anchor_status)
                if outcome_relation == "attempts"
                else outcome_relation
            )
            add_edge(
                source=outcome_anchor_id,
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
        incoming_edges_by_node: dict[str, list[SessionGraphEdgeRead]] = {}
        for edge in edges_by_id.values():
            outgoing_edges_by_node.setdefault(edge.source, []).append(edge)
            incoming_edges_by_node.setdefault(edge.target, []).append(edge)

        action_node_count = sum(
            1 for candidate in normalized_nodes_by_id.values() if candidate.node_type == "action"
        )
        preserved_path_node_ids = self._best_path_node_ids_for_prune(
            normalized_nodes_by_id=normalized_nodes_by_id,
            outgoing_edges_by_node=outgoing_edges_by_node,
            incoming_edges_by_node=incoming_edges_by_node,
        )

        removable_node_ids = {
            node_id
            for node_id, node in normalized_nodes_by_id.items()
            if self._should_remove_low_signal_execution_node(
                node=node,
                execution_node_count=action_node_count,
                preserved_path_node_ids=preserved_path_node_ids,
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
        execution_node_count: int,
        preserved_path_node_ids: set[str],
    ) -> bool:
        if node.node_type != "action":
            return False
        if execution_node_count < self._PRUNE_EXECUTION_THRESHOLD:
            return False
        if node.id in preserved_path_node_ids:
            return False
        if self._node_has_preserved_status(node):
            return False
        if bool(node.data.get("related_findings")) or bool(node.data.get("related_hypotheses")):
            return False
        if self._coerce_int(node.data.get("collaboration_value")) >= 70:
            return False
        if self._has_meaningful_execution_observation_data(node.data):
            return self._coerce_int(node.data.get("attempts_count")) <= 1
        if self._has_action_summary_signal(node.data):
            return self._coerce_int(node.data.get("attempts_count")) <= 1
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

    def _best_path_node_ids_for_prune(
        self,
        *,
        normalized_nodes_by_id: dict[str, SessionGraphNodeRead],
        outgoing_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
        incoming_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
    ) -> set[str]:
        actions = [node for node in normalized_nodes_by_id.values() if node.node_type == "action"]
        if not actions:
            return set()

        anchor = max(actions, key=self._best_path_anchor_sort_key)
        preserved_node_ids: set[str] = {anchor.id}
        anchor_task_id = self._pick_first_str(anchor.data.get("task_id"))

        if anchor_task_id is not None and anchor_task_id in normalized_nodes_by_id:
            preserved_node_ids.update(
                self._task_context_chain_ids(
                    task_id=anchor_task_id,
                    normalized_nodes_by_id=normalized_nodes_by_id,
                    incoming_edges_by_node=incoming_edges_by_node,
                )
            )
        else:
            root_node = next(
                (node for node in normalized_nodes_by_id.values() if node.node_type == "root"),
                None,
            )
            if root_node is not None:
                preserved_node_ids.add(root_node.id)

        action_ancestry = self._action_chain_to_anchor_ids(
            action_id=anchor.id,
            task_id=anchor_task_id,
            normalized_nodes_by_id=normalized_nodes_by_id,
            incoming_edges_by_node=incoming_edges_by_node,
        )
        preserved_node_ids.update(action_ancestry)
        preserved_node_ids.update(
            self._action_tail_to_outcome_ids(
                action_id=anchor.id,
                task_id=anchor_task_id,
                normalized_nodes_by_id=normalized_nodes_by_id,
                outgoing_edges_by_node=outgoing_edges_by_node,
            )
        )
        return preserved_node_ids

    def _best_path_anchor_sort_key(self, node: SessionGraphNodeRead) -> tuple[int, int, int, int]:
        status = str(node.data.get("status") or "")
        reasons = {
            item
            for item in self._coerce_list(node.data.get("milestone_reasons"))
            if isinstance(item, str)
        }
        priority = 0
        if self._node_is_active(node):
            priority = 6
        elif status in {"blocked", "failed"}:
            priority = 5
        elif "outcome" in reasons:
            priority = 4
        elif bool(node.data.get("related_findings")):
            priority = 3
        elif bool(node.data.get("related_hypotheses")):
            priority = 2
        elif self._has_meaningful_execution_observation_data(node.data):
            priority = 1
        collaboration = self._coerce_int(node.data.get("collaboration_value"))
        attempts = self._coerce_int(node.data.get("attempts_count"))
        sequence = self._coerce_int(node.data.get("sequence"))
        return (priority, collaboration, attempts, sequence)

    def _task_context_chain_ids(
        self,
        *,
        task_id: str,
        normalized_nodes_by_id: dict[str, SessionGraphNodeRead],
        incoming_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
    ) -> list[str]:
        chain: list[str] = []
        current_task_id: str | None = task_id
        visited: set[str] = set()
        while current_task_id is not None and current_task_id not in visited:
            node = normalized_nodes_by_id.get(current_task_id)
            if node is None or node.node_type not in {"task", "root"}:
                break
            visited.add(current_task_id)
            chain.append(current_task_id)
            incoming_edge = self._choose_best_task_context_edge(
                task_id=current_task_id,
                normalized_nodes_by_id=normalized_nodes_by_id,
                incoming_edges_by_node=incoming_edges_by_node,
            )
            if incoming_edge is None:
                break
            current_task_id = incoming_edge.source
        chain.reverse()
        return chain

    def _choose_best_task_context_edge(
        self,
        *,
        task_id: str,
        normalized_nodes_by_id: dict[str, SessionGraphNodeRead],
        incoming_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
    ) -> SessionGraphEdgeRead | None:
        candidates = [
            edge
            for edge in incoming_edges_by_node.get(task_id, [])
            if normalized_nodes_by_id.get(edge.source) is not None
            and normalized_nodes_by_id[edge.source].node_type in {"task", "root"}
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda edge: (
                2 if normalized_nodes_by_id[edge.source].node_type == "root" else 1,
                self._coerce_int(normalized_nodes_by_id[edge.source].data.get("sequence")),
                edge.source,
            ),
        )

    def _action_chain_to_anchor_ids(
        self,
        *,
        action_id: str,
        task_id: str | None,
        normalized_nodes_by_id: dict[str, SessionGraphNodeRead],
        incoming_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
    ) -> list[str]:
        chain = [action_id]
        current_action_id = action_id
        visited = {action_id}
        while True:
            previous_edge = self._choose_best_preceding_action_edge(
                action_id=current_action_id,
                task_id=task_id,
                normalized_nodes_by_id=normalized_nodes_by_id,
                incoming_edges_by_node=incoming_edges_by_node,
            )
            if previous_edge is None or previous_edge.source in visited:
                break
            visited.add(previous_edge.source)
            chain.insert(0, previous_edge.source)
            current_action_id = previous_edge.source
        return chain

    def _choose_best_preceding_action_edge(
        self,
        *,
        action_id: str,
        task_id: str | None,
        normalized_nodes_by_id: dict[str, SessionGraphNodeRead],
        incoming_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
    ) -> SessionGraphEdgeRead | None:
        candidates = []
        for edge in incoming_edges_by_node.get(action_id, []):
            if edge.relation != "precedes":
                continue
            source_node = normalized_nodes_by_id.get(edge.source)
            if source_node is None or source_node.node_type != "action":
                continue
            if (
                task_id is not None
                and self._pick_first_str(source_node.data.get("task_id")) != task_id
            ):
                continue
            candidates.append(edge)
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda edge: self._best_path_anchor_sort_key(normalized_nodes_by_id[edge.source]),
        )

    def _action_tail_to_outcome_ids(
        self,
        *,
        action_id: str,
        task_id: str | None,
        normalized_nodes_by_id: dict[str, SessionGraphNodeRead],
        outgoing_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
    ) -> list[str]:
        tail: list[str] = []
        current_action_id = action_id
        visited = {action_id}
        while True:
            direct_outcome = next(
                (
                    edge.target
                    for edge in outgoing_edges_by_node.get(current_action_id, [])
                    if normalized_nodes_by_id.get(edge.target) is not None
                    and normalized_nodes_by_id[edge.target].node_type == "outcome"
                ),
                None,
            )
            if direct_outcome is not None:
                tail.append(direct_outcome)
                return tail

            next_edge = self._choose_best_following_action_edge(
                action_id=current_action_id,
                task_id=task_id,
                normalized_nodes_by_id=normalized_nodes_by_id,
                outgoing_edges_by_node=outgoing_edges_by_node,
            )
            if next_edge is None or next_edge.target in visited:
                return tail
            visited.add(next_edge.target)
            tail.append(next_edge.target)
            current_action_id = next_edge.target

    def _choose_best_following_action_edge(
        self,
        *,
        action_id: str,
        task_id: str | None,
        normalized_nodes_by_id: dict[str, SessionGraphNodeRead],
        outgoing_edges_by_node: dict[str, list[SessionGraphEdgeRead]],
    ) -> SessionGraphEdgeRead | None:
        candidates = []
        for edge in outgoing_edges_by_node.get(action_id, []):
            if edge.relation != "precedes":
                continue
            target_node = normalized_nodes_by_id.get(edge.target)
            if target_node is None or target_node.node_type != "action":
                continue
            if (
                task_id is not None
                and self._pick_first_str(target_node.data.get("task_id")) != task_id
            ):
                continue
            candidates.append(edge)
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda edge: self._best_path_anchor_sort_key(normalized_nodes_by_id[edge.target]),
        )

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

    def _build_candidate_execution_nodes(
        self,
        *,
        run: WorkflowRun,
        evidence_nodes: list[GraphNode],
        causal_nodes: list[GraphNode] | None,
        task_ids: set[str],
        task_name_to_id: dict[str, str],
        task_by_id: dict[str, TaskNode],
    ) -> list[ExecutionCandidate]:
        candidates: list[ExecutionCandidate] = []
        order = 0

        def append_candidate(
            *,
            payload: dict[str, object],
            fallback_label: str,
            source_graphs: list[str],
            relation_hint: str | None = None,
            candidate_id: str,
            prefer_sidecar: bool = False,
        ) -> None:
            nonlocal order
            order += 1
            candidate = self._make_execution_candidate(
                payload=payload,
                fallback_label=fallback_label,
                source_graphs=source_graphs,
                relation_hint=relation_hint,
                candidate_id=candidate_id,
                order=order,
                task_ids=task_ids,
                task_name_to_id=task_name_to_id,
                task_by_id=task_by_id,
            )
            if prefer_sidecar and not self._has_primary_execution_evidence(candidate.payload):
                if not self._has_collaboration_sidecar_signal(candidate.payload):
                    return
                candidate.payload["prefer_sidecar"] = True
            elif (
                prefer_sidecar
                and self._pick_first_str(
                    candidate.payload.get("command"),
                    candidate.payload.get("primary_command"),
                    candidate.payload.get("request_summary"),
                    candidate.payload.get("tool_name"),
                )
                is None
            ):
                candidate.payload["prefer_sidecar"] = True
            candidates.append(candidate)

        for node in sorted(
            evidence_nodes, key=lambda item: (item.stable_key, item.created_at, item.id)
        ):
            append_candidate(
                payload=dict(node.payload_json),
                fallback_label=node.label,
                source_graphs=["evidence"],
                candidate_id=f"evidence:{node.id}",
                prefer_sidecar=True,
            )

        execution_records = run.state_json.get("execution_records", [])
        if isinstance(execution_records, list):
            for index, record in enumerate(execution_records, start=1):
                if not isinstance(record, dict):
                    continue
                append_candidate(
                    payload=dict(record),
                    fallback_label=str(
                        record.get("command_or_action")
                        or record.get("request_summary")
                        or record.get("task_name")
                        or f"Action {index}"
                    ),
                    source_graphs=["workflow"],
                    candidate_id=f"execution:{index}",
                )

        hypothesis_updates = run.state_json.get("hypothesis_updates", [])
        if isinstance(hypothesis_updates, list):
            for index, update in enumerate(hypothesis_updates, start=1):
                if not isinstance(update, dict):
                    continue
                result = str(update.get("result") or "unknown")
                append_candidate(
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
                        update.get("kind") or update.get("summary") or f"Hypothesis {index}"
                    ),
                    source_graphs=["workflow"],
                    relation_hint="attempts",
                    candidate_id=f"hypothesis:{index}",
                    prefer_sidecar=True,
                )

        if causal_nodes:
            for node in sorted(
                causal_nodes, key=lambda item: (item.stable_key, item.created_at, item.id)
            ):
                payload = dict(node.payload_json)
                append_candidate(
                    payload={
                        **payload,
                        "task_name": payload.get("task"),
                        "related_findings": [self._finding_summary(payload, node.label)],
                    },
                    fallback_label=node.label,
                    source_graphs=["causal"],
                    relation_hint="confirms",
                    candidate_id=f"finding:{node.id}",
                    prefer_sidecar=True,
                )
        else:
            findings = run.state_json.get("findings", [])
            if isinstance(findings, list):
                for index, finding in enumerate(findings, start=1):
                    if not isinstance(finding, dict):
                        continue
                    label = str(finding.get("title") or finding.get("id") or f"Finding {index}")
                    append_candidate(
                        payload={
                            **finding,
                            "task_name": finding.get("task"),
                            "related_findings": [self._finding_summary(finding, label)],
                        },
                        fallback_label=label,
                        source_graphs=["workflow"],
                        relation_hint="confirms",
                        candidate_id=f"finding:{index}",
                        prefer_sidecar=True,
                    )

        return self._collapse_context_sidecars(candidates)

    def _make_execution_candidate(
        self,
        *,
        payload: dict[str, object],
        fallback_label: str,
        source_graphs: list[str],
        relation_hint: str | None,
        candidate_id: str,
        order: int,
        task_ids: set[str],
        task_name_to_id: dict[str, str],
        task_by_id: dict[str, TaskNode],
    ) -> ExecutionCandidate:
        task_anchor_id = self._resolve_task_anchor_id(
            task_id=payload.get("task_id") or payload.get("task_node_id"),
            task_name=payload.get("task_name") or payload.get("task"),
            task_ids=task_ids,
            task_name_to_id=task_name_to_id,
        )
        task_anchor = task_by_id.get(task_anchor_id) if task_anchor_id is not None else None
        effective_source_graphs = self._merge_source_graphs(
            ["task"] if task_anchor_id is not None else [],
            source_graphs,
        )
        action_data = self._execution_payload(payload, source_graphs=effective_source_graphs)
        action_data["candidate_id"] = candidate_id
        action_data["task_id"] = task_anchor_id or action_data.get("task_id")
        action_data["task_name"] = (
            task_anchor.name if task_anchor is not None else action_data.get("task_name")
        )
        action_data["stage_key"] = (
            task_anchor.metadata_json.get("stage_key")
            if task_anchor is not None
            else payload.get("stage_key")
        )
        action_data["stage_category"] = self._classify_execution_stage(
            self._pick_first_str(
                action_data.get("stage_key"),
                action_data.get("task_name"),
                payload.get("stage_key"),
                payload.get("task_name"),
            )
        )
        action_data["blocked_reason"] = payload.get("blocked_reason") or payload.get("last_error")
        action_data["relation_hint"] = relation_hint
        action_data["sequence"] = order
        family_key = self._build_execution_family_key(action_data)
        action_data["family_key"] = family_key
        return ExecutionCandidate(
            candidate_id=candidate_id,
            fallback_label=fallback_label,
            payload=action_data,
            source_graphs=effective_source_graphs,
            relation_hint=relation_hint,
            task_id=task_anchor_id,
            order=order,
            family_key=family_key,
        )

    def _build_execution_family_key(self, payload: dict[str, object]) -> str:
        task_scope = (
            self._pick_first_str(
                payload.get("task_id"),
                payload.get("task_name"),
                payload.get("source_message_id"),
                payload.get("message_id"),
            )
            or "global"
        )
        tool_name = self._pick_first_str(payload.get("tool_name"), payload.get("tool"))
        normalized_tool_name = tool_name.casefold() if tool_name else None
        normalized_target = self._normalized_command_target(
            self._pick_first_str(payload.get("command")),
            self._pick_first_str(payload.get("request_summary")),
        )

        normalized_intent = self._normalized_execution_intent(
            self._pick_first_str(
                payload.get("request_summary"),
                payload.get("command"),
                payload.get("thought"),
                payload.get("summary"),
            )
        )
        if normalized_target:
            if normalized_tool_name and normalized_intent:
                return (
                    f"scope-target:{task_scope}:{normalized_tool_name}:"
                    f"{normalized_target}:{normalized_intent}"
                )
            if normalized_tool_name:
                return f"scope-target:{task_scope}:{normalized_tool_name}:{normalized_target}"
            if normalized_intent:
                return f"scope-target:{task_scope}:{normalized_target}:{normalized_intent}"
            return f"scope-target:{task_scope}:{normalized_target}"
        if normalized_intent:
            if normalized_tool_name:
                return f"scope-intent:{task_scope}:{normalized_tool_name}:{normalized_intent}"
            return f"scope-intent:{task_scope}:{normalized_intent}"

        trace_id = self._pick_first_str(payload.get("trace_id"), payload.get("tool_call_id"))
        if trace_id:
            return f"trace:{trace_id}"

        fallback = self._normalized_execution_intent(
            self._pick_first_str(
                payload.get("observation_summary"),
                payload.get("response_excerpt"),
                payload.get("stdout"),
                payload.get("tool_name"),
            )
        )
        return f"scope-fallback:{task_scope}:{fallback or 'step'}"

    def _collapse_context_sidecars(
        self, candidates: Sequence[ExecutionCandidate]
    ) -> list[ExecutionCandidate]:
        if not candidates:
            return []

        merged_candidates: list[ExecutionCandidate] = []
        pending_sidecars: list[ExecutionCandidate] = []
        for candidate in sorted(candidates, key=lambda item: (item.order, item.candidate_id)):
            if not self._is_context_sidecar_candidate(candidate):
                merged_candidates.append(candidate)
                continue

            pending_sidecars.append(candidate)

        for candidate in pending_sidecars:
            target = self._find_context_sidecar_target(candidate, merged_candidates)
            if target is None:
                merged_candidates.append(candidate)
                continue

            target.payload = self._merge_attack_node_data(target.payload, candidate.payload)
            target.payload["_merged_sidecar_count"] = (
                self._coerce_int(target.payload.get("_merged_sidecar_count")) + 1
            )
            existing_sidecar_candidates = target.payload.get("_merged_sidecar_candidates")
            merged_sidecar_candidates = (
                list(existing_sidecar_candidates)
                if isinstance(existing_sidecar_candidates, list)
                else []
            )
            if candidate.candidate_id not in merged_sidecar_candidates:
                merged_sidecar_candidates.append(candidate.candidate_id)
            target.payload["_merged_sidecar_candidates"] = merged_sidecar_candidates
            target.payload["source_graphs"] = self._merge_source_graphs(
                target.payload.get("source_graphs"),
                candidate.payload.get("source_graphs"),
                target.source_graphs,
                candidate.source_graphs,
            )
            target.source_graphs = self._merge_source_graphs(
                target.source_graphs,
                candidate.source_graphs,
            )
            target.relation_hint = self._prefer_relation_hint(
                target.relation_hint,
                candidate.relation_hint,
            )

        return sorted(merged_candidates, key=lambda item: (item.order, item.candidate_id))

    def _is_context_sidecar_candidate(self, candidate: ExecutionCandidate) -> bool:
        if bool(candidate.payload.get("prefer_sidecar")):
            return True
        if bool(candidate.payload.get("command")):
            return False
        if self._pick_first_str(
            candidate.payload.get("tool_name"),
            candidate.payload.get("tool"),
            candidate.payload.get("request_summary"),
        ):
            return False
        if self._has_distinct_execution_observation_signal(candidate.payload):
            return False
        return bool(
            candidate.payload.get("related_findings") or candidate.payload.get("related_hypotheses")
        )

    def _find_context_sidecar_target(
        self,
        candidate: ExecutionCandidate,
        merged_candidates: Sequence[ExecutionCandidate],
    ) -> ExecutionCandidate | None:
        trace_id = self._pick_first_str(candidate.payload.get("trace_id"))
        task_id = candidate.task_id or self._pick_first_str(candidate.payload.get("task_id"))
        eligible = [
            existing
            for existing in merged_candidates
            if existing.task_id == task_id and not self._is_context_sidecar_candidate(existing)
        ]
        if not eligible:
            return None

        if trace_id:
            same_trace = [
                existing
                for existing in eligible
                if self._pick_first_str(existing.payload.get("trace_id")) == trace_id
            ]
            if same_trace:
                eligible = same_trace

        prioritized = sorted(
            eligible,
            key=lambda existing: (
                (
                    1
                    if str(existing.payload.get("status") or "")
                    in {"in_progress", "blocked", "failed", "completed", "done"}
                    else 0
                ),
                1 if self._has_action_summary_signal(existing.payload) else 0,
                1 if self._has_meaningful_execution_observation_data(existing.payload) else 0,
                existing.order,
            ),
        )
        return prioritized[-1] if prioritized else None

    def _aggregate_execution_candidates(
        self, candidates: Sequence[ExecutionCandidate]
    ) -> list[ExecutionMilestone]:
        grouped: dict[str, list[ExecutionCandidate]] = {}
        for candidate in sorted(candidates, key=lambda item: (item.order, item.candidate_id)):
            grouped.setdefault(candidate.family_key, []).append(candidate)

        milestones: list[ExecutionMilestone] = []
        for family_key, family_candidates in grouped.items():
            ordered_family = sorted(
                family_candidates, key=lambda item: (item.order, item.candidate_id)
            )
            merged_data: dict[str, object] = {}
            relation_hint: str | None = None
            for candidate in ordered_family:
                merged_data = self._merge_attack_node_data(merged_data, candidate.payload)
                relation_hint = self._prefer_relation_hint(relation_hint, candidate.relation_hint)

            attempts_count = len(ordered_family) + sum(
                self._coerce_int(candidate.payload.get("_merged_sidecar_count"))
                for candidate in ordered_family
            )
            primary_command = self._pick_first_str(
                *(candidate.payload.get("command") for candidate in ordered_family)
            )
            best_observation_summary = self._best_execution_observation_summary(ordered_family)
            if best_observation_summary:
                merged_data["observation_summary"] = best_observation_summary
            if primary_command:
                merged_data["command"] = primary_command

            merged_data["family_key"] = family_key
            merged_data["attempts_count"] = attempts_count
            merged_from: list[str] = [candidate.candidate_id for candidate in ordered_family]
            for candidate in ordered_family:
                for sidecar_candidate_id in self._coerce_list(
                    candidate.payload.get("_merged_sidecar_candidates")
                ):
                    if (
                        isinstance(sidecar_candidate_id, str)
                        and sidecar_candidate_id not in merged_from
                    ):
                        merged_from.append(sidecar_candidate_id)
            merged_data["merged_from"] = merged_from
            merged_data["primary_command"] = primary_command
            merged_data["best_observation_summary"] = best_observation_summary
            merged_data["latest_status"] = ordered_family[-1].payload.get("status")
            merged_data["first_seen_at"] = self._candidate_timestamp(ordered_family[0])
            merged_data["last_seen_at"] = self._candidate_timestamp(ordered_family[-1])
            merged_data["sequence"] = ordered_family[0].order
            merged_data["stage_category"] = self._classify_execution_stage(
                self._pick_first_str(
                    merged_data.get("stage_key"),
                    merged_data.get("task_name"),
                    ordered_family[0].payload.get("stage_key"),
                    ordered_family[0].payload.get("task_name"),
                )
            )
            merged_data["aggregation_kind"] = "family" if attempts_count > 1 else "single"

            node_id = self._execution_milestone_id(merged_data, family_key)
            label = self._execution_milestone_label(ordered_family, merged_data)
            milestones.append(
                ExecutionMilestone(
                    node_id=node_id,
                    label=label,
                    data=merged_data,
                    task_id=self._pick_first_str(merged_data.get("task_id")),
                    family_key=family_key,
                    first_order=ordered_family[0].order,
                    last_order=ordered_family[-1].order,
                    relation_hint=relation_hint,
                    score=0,
                    reasons=(),
                )
            )

        return sorted(
            milestones,
            key=lambda item: (item.first_order, item.last_order, item.node_id),
        )

    def _select_milestone_execution_nodes(
        self,
        *,
        candidates: Sequence[ExecutionCandidate],
        run_status: WorkflowRunStatus,
        current_stage: str | None,
        ordered_tasks: Sequence[TaskNode],
    ) -> list[ExecutionMilestone]:
        milestones = self._aggregate_execution_candidates(candidates)
        if not milestones:
            return []

        milestones_by_task: dict[str | None, list[ExecutionMilestone]] = {}
        for milestone in milestones:
            milestones_by_task.setdefault(milestone.task_id, []).append(milestone)
        for grouped_milestones in milestones_by_task.values():
            grouped_milestones.sort(
                key=lambda item: (item.first_order, item.last_order, item.node_id)
            )

        last_substantive_by_task: dict[str, str] = {}
        for task_id, grouped_milestones in milestones_by_task.items():
            if task_id is None:
                continue
            substantive = [
                milestone
                for milestone in grouped_milestones
                if self._has_meaningful_execution_observation_data(milestone.data)
                or self._has_action_summary_signal(milestone.data)
                or bool(milestone.data.get("related_findings"))
                or bool(milestone.data.get("related_hypotheses"))
            ]
            if substantive:
                last_substantive_by_task[task_id] = substantive[-1].node_id

        selected_ids: set[str] = set()
        scored: dict[str, tuple[int, list[str]]] = {}
        seen_findings: set[tuple[object, ...]] = set()
        seen_hypotheses: set[tuple[object, ...]] = set()
        highest_stage_rank = -1
        current_task_id = self._current_stage_task_id(
            ordered_tasks,
            current_stage=current_stage,
            task_stage_to_id={
                str(task.metadata_json.get("stage_key")): task.id
                for task in ordered_tasks
                if isinstance(task.metadata_json.get("stage_key"), str)
            },
        )
        outcome_candidate_id = self._outcome_supporting_milestone_id(
            milestones=milestones,
            run_status=run_status,
        )

        for milestone in milestones:
            reasons: list[str] = []
            score = 0
            status = str(milestone.data.get("status") or "")
            task_milestones = milestones_by_task.get(milestone.task_id, [])
            is_first_in_task = (
                bool(task_milestones) and task_milestones[0].node_id == milestone.node_id
            )
            stage_rank = self._stage_rank(str(milestone.data.get("stage_category") or "unknown"))

            if self._milestone_is_active(milestone):
                score += 120
                reasons.append("active")
            if status in {"blocked", "failed"}:
                score += 110
                reasons.append("blocked")

            finding_keys = self._collect_related_item_keys(
                milestone.data.get("related_findings"),
                ("id", "title", "label"),
            )
            if any(key not in seen_findings for key in finding_keys):
                score += 85
                reasons.append("finding")

            hypothesis_keys = self._collect_related_item_keys(
                milestone.data.get("related_hypotheses"),
                ("trace_id", "summary", "kind", "index"),
            )
            if any(key not in seen_hypotheses for key in hypothesis_keys):
                score += 70
                reasons.append("hypothesis")

            if is_first_in_task and stage_rank > highest_stage_rank and stage_rank >= 1:
                score += 40
                reasons.append("stage_transition")
                highest_stage_rank = stage_rank

            if (
                milestone.task_id is not None
                and last_substantive_by_task.get(milestone.task_id) == milestone.node_id
            ):
                score += 20
                reasons.append("task_leaf")

            if milestone.node_id == outcome_candidate_id:
                score += 95
                reasons.append("outcome")

            attempts_count = self._coerce_int(milestone.data.get("attempts_count"))
            if attempts_count > 1:
                score += 35
                reasons.append("aggregated")

            if self._has_meaningful_execution_observation_data(milestone.data):
                score += 25
                reasons.append("observation")

            if self._has_action_summary_signal(milestone.data):
                score += 15
                reasons.append("action_signal")

            if self._is_milestone_action(
                milestone=milestone,
                score=score,
                reasons=reasons,
            ):
                selected_ids.add(milestone.node_id)

            scored[milestone.node_id] = (score, reasons)
            seen_findings.update(finding_keys)
            seen_hypotheses.update(hypothesis_keys)

        if current_task_id is not None and current_task_id in milestones_by_task:
            if not any(
                milestone.node_id in selected_ids
                for milestone in milestones_by_task[current_task_id]
            ):
                representative = max(
                    milestones_by_task[current_task_id],
                    key=lambda item: (
                        scored.get(item.node_id, (0, []))[0],
                        item.last_order,
                        item.node_id,
                    ),
                )
                representative_score, representative_reasons = scored.get(
                    representative.node_id, (0, [])
                )
                if representative_score >= 55:
                    selected_ids.add(representative.node_id)
                    representative_reasons.append("current_task")

        if not selected_ids:
            representative = max(
                milestones,
                key=lambda item: (
                    scored.get(item.node_id, (0, []))[0],
                    item.last_order,
                    item.node_id,
                ),
            )
            score, reasons = scored.get(representative.node_id, (0, []))
            if score >= 35:
                selected_ids.add(representative.node_id)
                reasons.append("representative")

        selected: list[ExecutionMilestone] = []
        for milestone in milestones:
            if milestone.node_id not in selected_ids:
                continue
            score, reasons = scored.get(milestone.node_id, (0, []))
            milestone_data = dict(milestone.data)
            milestone_data["collaboration_value"] = score
            milestone_data["milestone_reasons"] = list(dict.fromkeys(reasons))
            selected.append(
                ExecutionMilestone(
                    node_id=milestone.node_id,
                    label=milestone.label,
                    data=milestone_data,
                    task_id=milestone.task_id,
                    family_key=milestone.family_key,
                    first_order=milestone.first_order,
                    last_order=milestone.last_order,
                    relation_hint=milestone.relation_hint,
                    score=score,
                    reasons=tuple(dict.fromkeys(reasons)),
                )
            )

        return selected

    def _is_milestone_action(
        self,
        *,
        milestone: ExecutionMilestone,
        score: int,
        reasons: Sequence[str],
    ) -> bool:
        if any(
            reason
            in {
                "active",
                "blocked",
                "finding",
                "outcome",
            }
            for reason in reasons
        ):
            return True
        if "hypothesis" in reasons and score >= 70:
            return True
        if self._coerce_int(milestone.data.get("attempts_count")) > 1 and score >= 50:
            return True
        if (
            self._has_action_summary_signal(milestone.data)
            and self._has_meaningful_execution_observation_data(milestone.data)
            and score >= 35
        ):
            return True
        if score >= 85:
            return True
        return False

    def _pick_outcome_anchor_id(
        self,
        *,
        milestones: Sequence[ExecutionMilestone],
        run_status: WorkflowRunStatus,
        current_stage_task_id: str | None,
        root_node_id: str | None,
    ) -> str | None:
        supporting_id = self._outcome_supporting_milestone_id(
            milestones=milestones,
            run_status=run_status,
        )
        if supporting_id is not None:
            return supporting_id
        if current_stage_task_id is not None:
            return current_stage_task_id
        return root_node_id

    def _candidate_timestamp(self, candidate: ExecutionCandidate) -> object:
        return (
            candidate.payload.get("completed_at")
            or candidate.payload.get("updated_at")
            or candidate.order
        )

    def _execution_milestone_id(self, payload: dict[str, object], family_key: str) -> str:
        trace_id = self._pick_first_str(payload.get("trace_id"), payload.get("tool_call_id"))
        if trace_id:
            return f"action:{trace_id}"
        return f"action:{self._normalize_merge_label(family_key)}"

    def _execution_milestone_label(
        self,
        candidates: Sequence[ExecutionCandidate],
        payload: dict[str, object],
    ) -> str:
        first_candidate = candidates[0] if candidates else None
        return self._truncate_label(
            self._pick_first_str(
                payload.get("primary_command"),
                payload.get("command"),
                payload.get("summary"),
                payload.get("request_summary"),
                payload.get("tool_name"),
                payload.get("thought"),
                payload.get("best_observation_summary"),
                payload.get("observation_summary"),
                first_candidate.fallback_label if first_candidate is not None else None,
            )
            or "Action",
            fallback="Action",
        )

    def _prefer_relation_hint(
        self, current_hint: str | None, incoming_hint: str | None
    ) -> str | None:
        priority = {"blocks": 3, "confirms": 2, "attempts": 1}
        if incoming_hint is None:
            return current_hint
        if current_hint is None:
            return incoming_hint
        return (
            incoming_hint
            if priority.get(incoming_hint, 0) > priority.get(current_hint, 0)
            else current_hint
        )

    def _best_execution_observation_summary(
        self, candidates: Sequence[ExecutionCandidate]
    ) -> str | None:
        best: str | None = None
        for candidate in candidates:
            value = self._pick_first_str(
                candidate.payload.get("observation_summary"),
                candidate.payload.get("response_excerpt"),
                candidate.payload.get("stdout"),
                candidate.payload.get("stderr"),
            )
            if value is None or not self._is_meaningful_text(value):
                continue
            if best is None or len(value.strip()) > len(best.strip()):
                best = value.strip()
        return best

    def _collect_related_item_keys(
        self,
        value: object,
        key_fields: tuple[str, ...],
    ) -> set[tuple[object, ...]]:
        keys: set[tuple[object, ...]] = set()
        if not isinstance(value, list):
            return keys
        for item in value:
            if not isinstance(item, dict):
                continue
            keys.add(tuple(item.get(field) for field in key_fields))
        return keys

    def _outcome_supporting_milestone_id(
        self,
        *,
        milestones: Sequence[ExecutionMilestone],
        run_status: WorkflowRunStatus,
    ) -> str | None:
        if not milestones:
            return None
        preferred_statuses = (
            {"completed", "done"}
            if run_status is WorkflowRunStatus.DONE
            else (
                {"blocked", "failed"}
                if run_status in {WorkflowRunStatus.BLOCKED, WorkflowRunStatus.ERROR}
                else {"in_progress", "blocked", "failed", "completed", "done"}
            )
        )
        candidates = [
            milestone
            for milestone in milestones
            if str(milestone.data.get("status") or "") in preferred_statuses
        ]
        if not candidates:
            candidates = list(milestones)
        best = max(
            candidates,
            key=lambda item: (
                1 if self._milestone_is_active(item) else 0,
                1 if str(item.data.get("status") or "") in {"blocked", "failed"} else 0,
                1 if str(item.data.get("status") or "") in {"completed", "done"} else 0,
                len(self._coerce_list(item.data.get("related_findings"))),
                len(self._coerce_list(item.data.get("related_hypotheses"))),
                1 if self._has_meaningful_execution_observation_data(item.data) else 0,
                1 if self._has_action_summary_signal(item.data) else 0,
                self._coerce_int(item.data.get("collaboration_value")),
                self._coerce_int(item.data.get("attempts_count")),
                item.last_order,
            ),
        )
        return best.node_id

    def _milestone_is_active(self, milestone: ExecutionMilestone) -> bool:
        return bool(
            milestone.data.get("current")
            or milestone.data.get("active")
            or milestone.data.get("status") == "in_progress"
        )

    def _classify_execution_stage(self, value: str | None) -> str:
        if not value:
            return "unknown"
        normalized = value.casefold()
        if any(token in normalized for token in ("exploit", "weapon", "inject", "execute")):
            return "exploit"
        if any(token in normalized for token in ("pivot", "lateral", "post", "movement")):
            return "pivot"
        if any(
            token in normalized for token in ("valid", "verify", "safe_validation", "auth", "check")
        ):
            return "validation"
        if any(token in normalized for token in ("report", "outcome", "summary", "impact")):
            return "outcome"
        if any(token in normalized for token in ("recon", "collect", "surface", "enum", "context")):
            return "reconnaissance"
        return "unknown"

    def _stage_rank(self, stage_category: str) -> int:
        return self._STAGE_RANKS.get(stage_category, self._STAGE_RANKS["unknown"])

    @staticmethod
    def _coerce_int(value: object) -> int:
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return 0
            try:
                return int(stripped)
            except ValueError:
                return 0
        return 0

    @staticmethod
    def _coerce_list(value: object) -> list[object]:
        return list(value) if isinstance(value, list) else []

    def _has_action_summary_signal(self, data: dict[str, object]) -> bool:
        summary = self._pick_first_str(
            data.get("primary_command"),
            data.get("command"),
            data.get("request_summary"),
            data.get("summary"),
        )
        if summary is None or not self._is_meaningful_text(summary):
            return False
        if self._is_weak_validation_probe(data, summary=summary):
            return bool(self._coerce_int(data.get("attempts_count")) > 1)
        return True

    def _has_meaningful_execution_observation_data(self, data: dict[str, object]) -> bool:
        if self._has_distinct_execution_observation_signal(data):
            return True
        observation_summary = self._pick_first_str(
            data.get("best_observation_summary"),
            data.get("observation_summary"),
        )
        if observation_summary is None or not self._is_meaningful_text(observation_summary):
            return False
        if self._is_weak_validation_probe(data, summary=observation_summary):
            return False
        action_summary = self._pick_first_str(
            data.get("summary"),
            data.get("request_summary"),
            data.get("command"),
            data.get("primary_command"),
            data.get("thought"),
        )
        if action_summary is not None and observation_summary.strip() == action_summary.strip():
            return False
        return True

    def _has_primary_execution_evidence(self, data: dict[str, object]) -> bool:
        if self._pick_first_str(data.get("command"), data.get("primary_command")) is not None:
            return True
        if (
            self._pick_first_str(data.get("request_summary")) is not None
            and self._pick_first_str(data.get("tool_name")) is not None
        ):
            return True
        if self._has_distinct_execution_observation_signal(data):
            return True
        status = str(data.get("status") or "")
        if status in self._PRESERVED_ATTACK_STATUS_VALUES:
            return True
        trace_id = self._pick_first_str(data.get("trace_id"), data.get("tool_call_id"))
        task_anchor = self._pick_first_str(data.get("task_id"), data.get("task_name"))
        return trace_id is not None and task_anchor is not None

    def _has_collaboration_sidecar_signal(self, data: dict[str, object]) -> bool:
        if self._coerce_list(data.get("related_findings")):
            return True
        if self._coerce_list(data.get("related_hypotheses")):
            return True
        blocked_reason = self._pick_first_str(data.get("blocked_reason"))
        if blocked_reason is not None and self._is_meaningful_text(blocked_reason):
            return True
        return str(data.get("status") or "") in {"blocked", "failed"}

    def _has_structured_result_signal(self, result: object) -> bool:
        if not isinstance(result, dict) or not result:
            return False
        for key, value in result.items():
            if key in {"ok", "success", "status", "exit_code", "code"} and value in {
                True,
                False,
                0,
                1,
                "ok",
                "success",
                "completed",
                "done",
            }:
                continue
            if isinstance(value, str) and self._is_meaningful_text(value):
                return True
            if isinstance(value, list | dict) and bool(value):
                return True
            if isinstance(value, int | float) and value not in {0, 1}:
                return True
        return False

    def _is_weak_validation_probe(self, data: dict[str, object], *, summary: str) -> bool:
        if self._has_collaboration_sidecar_signal(data):
            return False
        normalized = summary.casefold()
        command = self._pick_first_str(data.get("primary_command"), data.get("command")) or ""
        tool_name = self._pick_first_str(data.get("tool_name")) or ""
        stage_category = str(data.get("stage_category") or "")
        target = self._normalized_command_target(
            command, self._pick_first_str(data.get("request_summary"))
        )
        if (
            command.casefold().startswith("curl ")
            and target is not None
            and any(token in target for token in ("health", "status", "ready", "live", "version"))
        ):
            return True
        if tool_name.casefold() in {
            "execute",
            "bash",
            "shell",
            "mcp_execute",
            "mcp.execute",
        } and any(
            token in normalized for token in ("pwd", "whoami", "ls", "echo ", "status", "health")
        ):
            return True
        return stage_category == "validation" and any(
            token in normalized
            for token in (
                "health",
                "status",
                "verify",
                "check",
                "validate",
                "test connection",
                "ping",
            )
        )

    def _has_distinct_execution_observation_signal(self, data: dict[str, object]) -> bool:
        for field in ("response_excerpt", "stdout", "stderr", "observation"):
            value = data.get(field)
            if isinstance(value, str) and self._is_meaningful_text(value):
                return True
        result = data.get("result")
        if self._has_structured_result_signal(result):
            return True
        observation_summary = self._pick_first_str(
            data.get("best_observation_summary"),
            data.get("observation_summary"),
        )
        if observation_summary is None or not self._is_meaningful_text(observation_summary):
            return False
        summary = self._pick_first_str(data.get("summary"), data.get("thought"))
        if summary is None:
            return True
        return observation_summary.strip() != summary.strip()

    def _is_meaningful_text(self, value: str) -> bool:
        normalized = value.strip()
        if len(normalized) < 6:
            return False
        return normalized.casefold() not in {
            "ok",
            "done",
            "success",
            "completed",
            "running",
            "true",
            "false",
            "null",
        }

    def _normalized_command_target(
        self,
        command: str | None,
        request_summary: str | None,
    ) -> str | None:
        for value in (command, request_summary):
            if not value:
                continue
            url_match = self._URL_RE.search(value)
            if url_match:
                parts = urlsplit(url_match.group(0))
                path = parts.path.rstrip("/") or "/"
                return self._normalize_merge_label(f"{parts.netloc}{path}")
        if not command:
            return None
        tokens = [
            token.strip("\"'") for token in re.findall(r"[^\s\"']+|\"[^\"]*\"|'[^']*'", command)
        ]
        for token in tokens[1:]:
            if not token or token.startswith("-"):
                continue
            return self._normalize_merge_label(token)
        return None

    def _normalized_execution_intent(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = self._URL_RE.sub(" url ", value.casefold())
        normalized = re.sub(r"0x[a-f0-9]+", " ", normalized)
        normalized = re.sub(r"\b\d+\b", " ", normalized)
        normalized = re.sub(r"[\[\]{}()\"'`]+", " ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if not normalized:
            return None
        return self._normalize_merge_label(" ".join(normalized.split(" ")[:10]))

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
        conversation_status = self._conversation_status_to_workflow_status(outcome_status)
        milestone_actions = self._select_milestone_execution_nodes(
            candidates=self._build_conversation_execution_candidates(
                messages=ordered_messages,
                generations_by_message_id=generation_by_assistant_message_id,
            ),
            run_status=conversation_status,
            current_stage=None,
            ordered_tasks=(),
        )
        outcome_anchor_id = self._pick_outcome_anchor_id(
            milestones=milestone_actions,
            run_status=conversation_status,
            current_stage_task_id=None,
            root_node_id=goal_node_id,
        )
        best_path_milestone = next(
            (
                milestone
                for milestone in milestone_actions
                if milestone.node_id == outcome_anchor_id
            ),
            milestone_actions[-1] if milestone_actions else None,
        )

        if goal_node_id is not None and best_path_milestone is not None:
            root_summary: dict[str, object] = {
                "best_path_summary": best_path_milestone.label,
                "current_action_summary": self._pick_first_str(
                    best_path_milestone.data.get("summary"),
                    best_path_milestone.data.get("request_summary"),
                    best_path_milestone.data.get("primary_command"),
                    best_path_milestone.data.get("command"),
                    best_path_milestone.label,
                ),
            }
            key_observation = self._pick_first_str(
                best_path_milestone.data.get("best_observation_summary"),
                best_path_milestone.data.get("observation_summary"),
            )
            if key_observation is not None:
                root_summary["key_observation_summary"] = key_observation
            blocker = self._pick_first_str(best_path_milestone.data.get("blocked_reason"))
            if blocker is not None:
                root_summary["blocker"] = blocker
            add_node(
                node_id=goal_node_id,
                node_type="root",
                label=nodes_by_id[goal_node_id].label,
                data=root_summary,
            )

        supporting_milestones: list[ExecutionMilestone] = []
        seen_supporting_labels: set[str] = set()
        for milestone in sorted(
            milestone_actions,
            key=lambda item: (
                item.node_id == outcome_anchor_id,
                self._coerce_int(item.data.get("collaboration_value")),
                item.last_order,
            ),
            reverse=True,
        ):
            if milestone.label in seen_supporting_labels:
                continue
            seen_supporting_labels.add(milestone.label)
            supporting_milestones.append(milestone)
            if len(supporting_milestones) >= 3:
                break

        previous_node_id = goal_node_id if goal_node_id is not None else None
        for index, milestone in enumerate(milestone_actions):
            add_node(
                node_id=milestone.node_id,
                node_type="action",
                label=milestone.label,
                data=milestone.data,
            )
            if previous_node_id is not None:
                add_edge(
                    source=previous_node_id,
                    target=milestone.node_id,
                    relation="attempts" if index == 0 else "precedes",
                    data={"source_graphs": milestone.data.get("source_graphs", [])},
                )
            previous_node_id = milestone.node_id

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
                "supporting_actions": [milestone.label for milestone in supporting_milestones],
                "source_graphs": ["conversation", "generation"],
            },
        )
        if outcome_anchor_id is not None and outcome_anchor_id in nodes_by_id:
            add_edge(
                source=outcome_anchor_id,
                target=outcome_node_id,
                relation=(
                    "blocks"
                    if outcome_status in {"failed", "cancelled"}
                    else "confirms" if conversation_status is WorkflowRunStatus.DONE else "attempts"
                ),
                data={"source_graphs": ["conversation"]},
            )

        nodes_by_id, edges_by_id = self._prune_attack_graph_for_default_view(
            nodes_by_id=nodes_by_id,
            edges_by_id=edges_by_id,
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

    def _build_conversation_execution_candidates(
        self,
        *,
        messages: Sequence[Message],
        generations_by_message_id: dict[str, ChatGeneration],
    ) -> list[ExecutionCandidate]:
        candidates: list[ExecutionCandidate] = []
        order = 0
        for message in messages:
            if message.role is not MessageRole.ASSISTANT:
                continue

            generation = generations_by_message_id.get(message.id)
            transcript = resolve_message_assistant_transcript(message)
            payloads: dict[str, dict[str, object]] = {}
            payload_source_graphs: dict[str, list[str]] = {}
            reasoning_payloads: list[dict[str, object]] = []
            latest_payload_key: str | None = None
            tool_call_to_payload_key: dict[str, str] = {}
            message_scope = self._normalize_merge_label(message.id)

            def ensure_payload(key: str, source_graphs: list[str]) -> dict[str, object]:
                payload = payloads.get(key)
                if payload is None:
                    payload = {
                        "source_message_id": message.id,
                        "branch_id": message.branch_id,
                        "generation_id": generation.id if generation is not None else None,
                        "message_id": message.id,
                    }
                    payloads[key] = payload
                payload_source_graphs[key] = self._merge_source_graphs(
                    payload_source_graphs.get(key, []),
                    source_graphs,
                )
                return payload

            def conversation_family_key(
                *,
                trace_id: str | None = None,
                tool_name: str | None = None,
                command: str | None = None,
                request_summary: str | None = None,
                observation_text: str | None = None,
            ) -> str:
                normalized_target = self._normalized_command_target(command, request_summary)
                intent_source = self._pick_first_str(
                    request_summary,
                    command,
                    observation_text,
                    tool_name,
                )
                normalized_intent = self._normalized_execution_intent(intent_source)
                if normalized_target is not None:
                    return f"message:{message_scope}:target:{normalized_target}"
                if normalized_intent is not None:
                    return f"message:{message_scope}:intent:{normalized_intent}"
                if trace_id is not None:
                    return f"message:{message_scope}:trace:{self._normalize_merge_label(trace_id)}"
                return f"message:{message_scope}:summary"

            def choose_attach_key() -> str | None:
                if latest_payload_key is not None and latest_payload_key in payloads:
                    return latest_payload_key
                if not payloads:
                    return None
                return max(
                    payloads,
                    key=lambda key: (
                        1 if self._has_primary_execution_evidence(payloads[key]) else 0,
                        1 if self._has_meaningful_execution_observation_data(payloads[key]) else 0,
                        1 if self._has_action_summary_signal(payloads[key]) else 0,
                        key,
                    ),
                )

            for segment in transcript:
                if segment.kind == "reasoning":
                    reasoning_text = self._extract_reasoning_text(segment.text)
                    if not reasoning_text:
                        continue
                    reasoning_payloads.append(
                        {
                            "message_id": message.id,
                            "segment_id": segment.id,
                            "summary": reasoning_text,
                            "text": segment.text,
                        }
                    )
                    continue

                if segment.kind == "tool_call":
                    trace_id = str(segment.tool_call_id or segment.id)
                    command = self._read_segment_command(segment)
                    request_summary = self._pick_first_str(segment.title, segment.text)
                    payload_key = conversation_family_key(
                        trace_id=trace_id,
                        tool_name=segment.tool_name,
                        command=command,
                        request_summary=request_summary,
                    )
                    tool_call_to_payload_key[trace_id] = payload_key
                    payload = ensure_payload(payload_key, ["conversation", "transcript"])
                    payload.update(
                        {
                            "trace_id": payload.get("trace_id") or trace_id,
                            "tool_call_id": segment.tool_call_id,
                            "tool_name": segment.tool_name,
                            "arguments": dict(segment.metadata_payload),
                            "command": command,
                            "request_summary": request_summary,
                            "status": segment.status or payload.get("status"),
                            "updated_at": segment.updated_at.isoformat(),
                        }
                    )
                    latest_payload_key = payload_key
                    continue

                if segment.kind not in {"tool_result", "output", "error"}:
                    continue

                observation_data = self._conversation_observation_data(
                    message=message,
                    segment=segment,
                )
                observation_trace_id: str | None = self._pick_first_str(
                    observation_data.get("trace_id"),
                    observation_data.get("tool_call_id"),
                )
                observation_payload_key: str | None = (
                    tool_call_to_payload_key.get(observation_trace_id or "")
                    if observation_trace_id is not None
                    else None
                )
                if observation_payload_key is None:
                    observation_payload_key = latest_payload_key or conversation_family_key(
                        trace_id=observation_trace_id,
                        tool_name=self._pick_first_str(observation_data.get("tool_name")),
                        command=self._pick_first_str(observation_data.get("command")),
                        request_summary=self._pick_first_str(
                            observation_data.get("request_summary")
                        ),
                        observation_text=self._pick_first_str(
                            observation_data.get("observation_summary"),
                            observation_data.get("response_excerpt"),
                            observation_data.get("stdout"),
                            observation_data.get("stderr"),
                            observation_data.get("text"),
                        ),
                    )
                payload = ensure_payload(observation_payload_key, ["conversation", "transcript"])
                payload.update(observation_data)
                payload["trace_id"] = payload.get("trace_id") or observation_trace_id or segment.id
                payload["updated_at"] = segment.updated_at.isoformat()
                if segment.kind == "error":
                    payload["status"] = "failed"
                latest_payload_key = observation_payload_key

            reasoning_fragments = self._conversation_reasoning_fragments(
                message=message,
                generation=generation,
                transcript=transcript,
            )
            seen_reasoning: set[str] = set()
            merged_reasoning: list[dict[str, object]] = []
            for reasoning_payload in reasoning_payloads:
                summary = self._pick_first_str(reasoning_payload.get("summary"))
                if summary is None or summary.casefold() in seen_reasoning:
                    continue
                seen_reasoning.add(summary.casefold())
                merged_reasoning.append(reasoning_payload)
            for index, fragment in enumerate(reasoning_fragments, start=1):
                if fragment.casefold() in seen_reasoning:
                    continue
                seen_reasoning.add(fragment.casefold())
                merged_reasoning.append(
                    {
                        "message_id": message.id,
                        "generation_id": generation.id if generation is not None else None,
                        "summary": fragment,
                        "index": index,
                    }
                )

            message_text = self._extract_reasoning_text(message.content) or message.content.strip()
            if not payloads:
                if not self._is_meaningful_text(message_text) and not merged_reasoning:
                    continue
                payload_key = f"message:{message_scope}:summary"
                payload = ensure_payload(payload_key, ["conversation", "generation"])
                payload.update(
                    {
                        "trace_id": message.id,
                        "response_excerpt": message_text or payload.get("response_excerpt"),
                        "summary": message_text
                        or self._pick_first_str(
                            *(item.get("summary") for item in merged_reasoning)
                        ),
                        "observation_summary": message_text
                        or self._pick_first_str(
                            *(item.get("summary") for item in merged_reasoning)
                        ),
                        "status": (
                            generation.status.value
                            if generation is not None
                            else (
                                message.status.value if message.status is not None else "completed"
                            )
                        ),
                    }
                )
                latest_payload_key = payload_key
            else:
                attach_key = choose_attach_key()
                if attach_key is not None and self._is_meaningful_text(message_text):
                    payload = payloads[attach_key]
                    payload.setdefault("response_excerpt", message_text)
                    if not self._has_action_summary_signal(payload):
                        payload.setdefault("summary", message_text)
                    if not self._has_meaningful_execution_observation_data(payload):
                        payload.setdefault("observation_summary", message_text)
                    latest_payload_key = attach_key

            attach_key = choose_attach_key()
            if attach_key is not None and merged_reasoning:
                payload = payloads[attach_key]
                existing_hypotheses = self._coerce_list(payload.get("related_hypotheses"))
                payload["related_hypotheses"] = self._merge_related_items(
                    existing_hypotheses,
                    merged_reasoning,
                    ("trace_id", "summary", "kind", "index"),
                )
                payload.setdefault(
                    "thought",
                    self._pick_first_str(*(item.get("summary") for item in merged_reasoning)),
                )

            for payload_key, payload in payloads.items():
                if not self._has_primary_execution_evidence(
                    payload
                ) and not self._has_collaboration_sidecar_signal(payload):
                    continue
                if (
                    self._is_weak_validation_probe(
                        payload,
                        summary=self._pick_first_str(
                            payload.get("primary_command"),
                            payload.get("command"),
                            payload.get("request_summary"),
                            payload.get("summary"),
                        )
                        or "",
                    )
                    and self._coerce_int(payload.get("attempts_count")) <= 1
                ):
                    if not self._has_meaningful_execution_observation_data(payload):
                        continue
                order += 1
                payload_status = str(payload.get("status") or "")
                payload["status"] = payload_status or (
                    generation.status.value
                    if generation is not None
                    else (message.status.value if message.status is not None else "completed")
                )
                payload["trace_id"] = payload.get("trace_id") or f"{message.id}:{payload_key}"
                payload.setdefault(
                    "summary",
                    self._pick_first_str(
                        payload.get("request_summary"),
                        payload.get("command"),
                        payload.get("thought"),
                    ),
                )
                if not self._has_meaningful_execution_observation_data(payload):
                    observation_fallback = self._pick_first_str(
                        payload.get("response_excerpt"),
                        payload.get("summary"),
                    )
                    if observation_fallback is not None and self._is_meaningful_text(
                        observation_fallback
                    ):
                        payload.setdefault("observation_summary", observation_fallback)
                fallback_label = (
                    self._pick_first_str(
                        payload.get("command"),
                        payload.get("request_summary"),
                        payload.get("summary"),
                        message_text,
                        self._conversation_action_label(message=message, generation=generation),
                    )
                    or "Conversation action"
                )
                candidates.append(
                    self._make_execution_candidate(
                        payload=payload,
                        fallback_label=fallback_label,
                        source_graphs=payload_source_graphs.get(
                            payload_key, ["conversation", "generation"]
                        ),
                        relation_hint=(
                            "blocks"
                            if str(payload.get("status") or "") in {"failed", "cancelled"}
                            else "attempts"
                        ),
                        candidate_id=f"conversation:{payload_key}",
                        order=order,
                        task_ids=set(),
                        task_name_to_id={},
                        task_by_id={},
                    )
                )

        return candidates

    def _conversation_status_to_workflow_status(self, status: str) -> WorkflowRunStatus:
        normalized = status.strip().casefold()
        if normalized in {"failed", "cancelled", "error"}:
            return WorkflowRunStatus.ERROR
        if normalized in {"blocked"}:
            return WorkflowRunStatus.BLOCKED
        if normalized in {"running", "in_progress", "streaming"}:
            return WorkflowRunStatus.RUNNING
        return WorkflowRunStatus.DONE

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
        if not status_value:
            return "attempts"
        if status_value in {TaskNodeStatus.FAILED.value, TaskNodeStatus.BLOCKED.value}:
            return "blocks"
        if status_value in {TaskNodeStatus.IN_PROGRESS.value, TaskNodeStatus.READY.value}:
            return "attempts"
        if status_value in {TaskNodeStatus.COMPLETED.value, WorkflowRunStatus.DONE.value}:
            return "confirms"
        return "attempts"

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
            payload.get("result_summary"),
            payload.get("observation"),
            payload.get("stdout"),
            payload.get("stderr"),
            payload.get("response_excerpt"),
            payload.get("summary"),
            payload.get("text"),
        )
        return {
            "task_id": payload.get("task_id") or payload.get("task_node_id"),
            "task_name": payload.get("task_name") or payload.get("task"),
            "status": payload.get("status"),
            "current": payload.get("current") or payload.get("active"),
            "thought": payload.get("thought"),
            "summary": payload.get("summary") or payload.get("result_summary"),
            "tool_name": payload.get("tool_name"),
            "tool_call_id": payload.get("tool_call_id"),
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
            "branch_id": payload.get("branch_id"),
            "generation_id": payload.get("generation_id"),
            "message_id": payload.get("message_id"),
            "related_findings": (
                payload.get("related_findings")
                if isinstance(payload.get("related_findings"), list)
                else []
            ),
            "related_hypotheses": (
                payload.get("related_hypotheses")
                if isinstance(payload.get("related_hypotheses"), list)
                else []
            ),
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
            normalized_command = self._normalize_merge_label(command_summary)
            return f"task-tool:{task_anchor_id}:{tool_name.casefold()}:{normalized_command}"
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
