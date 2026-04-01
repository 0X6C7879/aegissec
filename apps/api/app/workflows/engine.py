from __future__ import annotations

from langgraph.graph import StateGraph

from app.workflows.states import WorkflowState
from app.workflows.template_loader import WorkflowTemplate


class DeterministicWorkflowEngine:
    def build_graph(self, template: WorkflowTemplate) -> StateGraph[WorkflowState]:
        if not template.stages:
            raise ValueError("Workflow template must define at least one stage.")
        return StateGraph(WorkflowState)

    def build_initial_state(
        self,
        *,
        session_id: str,
        template: WorkflowTemplate,
        skill_snapshot: list[dict[str, object]],
        mcp_snapshot: list[dict[str, object]],
        runtime_policy: dict[str, object],
        seed_message_id: str | None,
    ) -> WorkflowState:
        return WorkflowState(
            session_id=session_id,
            current_stage=template.stages[0].key,
            stage_order=[stage.key for stage in template.stages],
            messages=[],
            skill_snapshot=skill_snapshot,
            mcp_snapshot=mcp_snapshot,
            runtime_policy=runtime_policy,
            findings=[],
            graph_updates=[],
            seed_message_id=seed_message_id,
        )
