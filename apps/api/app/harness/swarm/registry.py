from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.db.models import utc_now


class SwarmAgentStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    IDLE = "idle"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class SwarmAgentRecord:
    agent_id: str
    profile_name: str
    parent_agent_id: str | None
    objective: str
    status: SwarmAgentStatus = SwarmAgentStatus.CREATED
    created_at: datetime = field(default_factory=utc_now)
    metadata: dict[str, object] = field(default_factory=dict)


class SwarmRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, SwarmAgentRecord] = {}

    def register(self, agent: SwarmAgentRecord) -> SwarmAgentRecord:
        self._agents[agent.agent_id] = agent
        return agent

    def get(self, agent_id: str) -> SwarmAgentRecord | None:
        return self._agents.get(agent_id)

    def list_agents(self) -> list[SwarmAgentRecord]:
        return list(self._agents.values())

    def update_status(self, agent_id: str, status: SwarmAgentStatus) -> SwarmAgentRecord | None:
        agent = self._agents.get(agent_id)
        if agent is None:
            return None
        agent.status = status
        return agent
