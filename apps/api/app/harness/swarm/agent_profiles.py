from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class SwarmAgentProfile:
    name: str
    role: str
    title: str
    instructions: str
    can_spawn: bool = False
    can_continue: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


def build_default_agent_profiles() -> dict[str, SwarmAgentProfile]:
    return {
        "coordinator": SwarmAgentProfile(
            name="coordinator",
            role="Coordinator",
            title="Coordinator",
            instructions=(
                "Own orchestration and synthesis. Spawn or continue workers only when it reduces "
                "risk or increases coverage. Workers must receive self-contained objectives. "
                "Do not offload final understanding; obey scope, approval, and runtime policy."
            ),
            can_spawn=True,
            can_continue=True,
        ),
        "planner_agent": SwarmAgentProfile(
            name="planner_agent",
            role="Planner",
            title="Planner",
            instructions=(
                "Decompose objectives into bounded tasks, dependencies, and acceptance checks. "
                "Prefer smaller executable units and explicit verification steps."
            ),
            can_continue=True,
        ),
        "recon_agent": SwarmAgentProfile(
            name="recon_agent",
            role="Recon",
            title="Recon",
            instructions=(
                "Gather facts and evidence conservatively. Prefer read-only work in parallel and "
                "surface observations with precise artifacts and limits."
            ),
            can_continue=True,
        ),
        "validator_agent": SwarmAgentProfile(
            name="validator_agent",
            role="Validator",
            title="Validator",
            instructions=(
                "Verify conclusions independently. Challenge assumptions, cross-check outputs, and "
                "report unresolved uncertainty explicitly."
            ),
            can_continue=True,
        ),
        "reflector_agent": SwarmAgentProfile(
            name="reflector_agent",
            role="Reflector",
            title="Reflector",
            instructions=(
                "Analyze failures, contradictions, and dead ends. Recommend replans or safer next "
                "steps grounded in observed evidence."
            ),
            can_continue=True,
        ),
    }
