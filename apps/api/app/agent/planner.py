from __future__ import annotations

from dataclasses import dataclass

from app.agent.workflow import PlannedTaskNode
from app.db.models import TaskNodeType
from app.workflows.template_loader import WorkflowTemplate


@dataclass(frozen=True)
class PlannedWorkflow:
    stage_order: list[str]
    nodes: list[PlannedTaskNode]
    summary: str


class Planner:
    def build_plan(self, *, goal: str, template: WorkflowTemplate) -> PlannedWorkflow:
        stage_order = [stage.key for stage in template.stages]
        ordered_stage_titles = {stage.key: stage.title for stage in template.stages}
        ordered_stage_roles = {stage.key: stage.role for stage in template.stages}
        ordered_stage_phases = {stage.key: stage.phase for stage in template.stages}
        ordered_stage_role_prompts = {stage.key: stage.role_prompt for stage in template.stages}
        ordered_stage_sub_agent_prompts = {
            stage.key: stage.sub_agent_role_prompt for stage in template.stages
        }
        stage_approvals = {stage.key: stage.requires_approval for stage in template.stages}

        nodes: list[PlannedTaskNode] = []
        sequence = 1
        previous_stage_key: str | None = None
        for stage_key in stage_order:
            stage_description = f"Stage: {ordered_stage_titles[stage_key]}"
            nodes.append(
                PlannedTaskNode(
                    planner_key=f"stage:{stage_key}",
                    name=stage_key,
                    node_type=stage_key_to_type(stage_key),
                    sequence=sequence,
                    stage_key=stage_key,
                    role=ordered_stage_roles[stage_key],
                    title=ordered_stage_titles[stage_key],
                    description=stage_description,
                    depends_on=((f"stage:{previous_stage_key}",) if previous_stage_key else ()),
                    parent_key=None,
                    priority=100,
                    approval_required=False,
                    metadata={
                        "kind": "stage",
                        "workflow_phase": ordered_stage_phases[stage_key],
                        "template_kinds": list(template.template_kinds),
                        "role_prompt": ordered_stage_role_prompts[stage_key],
                        "sub_agent_role_prompt": ordered_stage_sub_agent_prompts[stage_key],
                    },
                )
            )
            previous_stage_key = stage_key
            sequence += 1

        stage_tasks: dict[str, list[dict[str, object]]] = {
            "scope_guard": [
                {
                    "key": "scope_constraints",
                    "title": "确认范围与约束",
                    "description": (
                        "Extract scope, constraints, and explicit authorization boundaries."
                    ),
                    "priority": 95,
                    "depends_on": [],
                }
            ],
            "runtime_boot": [
                {
                    "key": "runtime_policy_check",
                    "title": "检查执行策略",
                    "description": "Validate runtime policy and command safety mode for this goal.",
                    "priority": 90,
                    "depends_on": ["scope_guard.scope_constraints"],
                }
            ],
            "skill_mcp_sync": [
                {
                    "key": "capability_snapshot",
                    "title": "能力快照",
                    "description": (
                        "Capture available Skill and MCP capability snapshot for planning."
                    ),
                    "priority": 86,
                    "depends_on": ["runtime_boot.runtime_policy_check"],
                }
            ],
            "context_collect": [
                {
                    "key": "attack_surface",
                    "title": "攻击面清点",
                    "description": (
                        "Collect target entry points and baseline signals "
                        "from the authorized scope."
                    ),
                    "priority": 82,
                    "depends_on": ["skill_mcp_sync.capability_snapshot"],
                },
                {
                    "key": "existing_evidence",
                    "title": "已有线索整理",
                    "description": (
                        "Organize historical evidence and environment "
                        "context into structured facts."
                    ),
                    "priority": 80,
                    "depends_on": ["skill_mcp_sync.capability_snapshot"],
                },
            ],
            "hypothesis_build": [
                {
                    "key": "hypothesis_draft",
                    "title": "假设生成",
                    "description": (
                        "Generate testable hypotheses from collected context and known constraints."
                    ),
                    "priority": 84,
                    "depends_on": [
                        "context_collect.attack_surface",
                        "context_collect.existing_evidence",
                    ],
                }
            ],
            "safe_validation": [
                {
                    "key": "validate_primary_hypothesis",
                    "title": "低风险验证执行",
                    "description": (
                        "Run low-risk validation checks and preserve full execution trace."
                    ),
                    "priority": 88,
                    "depends_on": ["hypothesis_build.hypothesis_draft"],
                }
            ],
            "findings_merge": [
                {
                    "key": "merge_findings",
                    "title": "发现归并",
                    "description": (
                        "Merge observations into normalized findings with confidence annotations."
                    ),
                    "priority": 76,
                    "depends_on": ["safe_validation.validate_primary_hypothesis"],
                }
            ],
            "causal_graph_update": [
                {
                    "key": "update_causal_chain",
                    "title": "因果链更新",
                    "description": "Link evidence and findings into causal chain relationships.",
                    "priority": 72,
                    "depends_on": ["findings_merge.merge_findings"],
                }
            ],
            "report_export": [
                {
                    "key": "report_summary",
                    "title": "报告导出",
                    "description": (
                        "Build reproducible session summary and exportable evidence index."
                    ),
                    "priority": 70,
                    "depends_on": ["causal_graph_update.update_causal_chain"],
                }
            ],
        }

        for stage_key in stage_order:
            role = ordered_stage_roles[stage_key]
            stage_title = ordered_stage_titles[stage_key]
            for raw_task in stage_tasks.get(stage_key, []):
                key = str(raw_task["key"])
                depends_on_raw = raw_task.get("depends_on", [])
                depends_on = (
                    [str(item) for item in depends_on_raw]
                    if isinstance(depends_on_raw, list)
                    else []
                )
                metadata: dict[str, object] = {
                    "goal": goal,
                    "kind": "task",
                    "stage_title": stage_title,
                    "workflow_phase": ordered_stage_phases[stage_key],
                    "template_kinds": list(template.template_kinds),
                    "role_prompt": ordered_stage_role_prompts[stage_key],
                    "sub_agent_role_prompt": ordered_stage_sub_agent_prompts[stage_key],
                }
                priority_raw = raw_task.get("priority", 50)
                priority = priority_raw if isinstance(priority_raw, int) else 50
                nodes.append(
                    PlannedTaskNode(
                        planner_key=f"task:{stage_key}.{key}",
                        name=f"{stage_key}.{key}",
                        node_type=stage_key_to_leaf_type(stage_key),
                        sequence=sequence,
                        stage_key=stage_key,
                        role=role,
                        title=str(raw_task["title"]),
                        description=str(raw_task["description"]),
                        depends_on=tuple(
                            [f"task:{dependency}" for dependency in depends_on]
                            + [f"stage:{stage_key}"]
                        ),
                        parent_key=f"stage:{stage_key}",
                        priority=priority,
                        approval_required=stage_approvals[stage_key],
                        metadata=metadata,
                    )
                )
                sequence += 1

        summary = (
            f"Authorized assessment plan for goal: {goal}. "
            "Includes staged DAG tasks with explicit dependencies and approval checkpoints."
        )
        return PlannedWorkflow(stage_order=stage_order, nodes=nodes, summary=summary)


def stage_key_to_type(_: str) -> TaskNodeType:
    return TaskNodeType.STAGE


def stage_key_to_leaf_type(_: str) -> TaskNodeType:
    return TaskNodeType.TASK
