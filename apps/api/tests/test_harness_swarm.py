import asyncio

from app.harness.state import HarnessSessionState
from app.harness.swarm import build_default_swarm_coordinator


def test_swarm_coordinator_spawn_send_stop_updates_state() -> None:
    async def _run() -> None:
        session_state = HarnessSessionState(session_id="sess-1", memory_key="sess-1")
        coordinator = build_default_swarm_coordinator(
            session_id="sess-1",
            session_state=session_state,
        )

        spawn_payload = await coordinator.spawn_agent(
            profile_name="planner_agent",
            objective="Plan the next attack path.",
        )

        agent_id = spawn_payload["agent"]["agent_id"]
        assert spawn_payload["agent"]["profile_name"] == "planner_agent"
        assert spawn_payload["task"]["status"] == "in_progress"
        assert any(item["status"] == "planned" for item in spawn_payload["notifications"])
        assert any(item["status"] == "started" for item in spawn_payload["notifications"])
        coordinator_agent_id = session_state.swarm.get("coordinator_agent_id")
        assert isinstance(coordinator_agent_id, str)
        assert coordinator_agent_id.startswith("coordinator-")
        agent_ids = session_state.swarm.get("agent_ids")
        assert isinstance(agent_ids, list)
        assert agent_id in agent_ids

        message_payload = await coordinator.send_message(
            agent_id=agent_id,
            content="Validate the plan assumptions.",
        )
        assert message_payload["agent_id"] == agent_id
        assert message_payload["message"]["kind"] == "user_message"
        assert any(item["status"] == "message" for item in message_payload["notifications"])

        stop_payload = await coordinator.stop_agent(agent_id=agent_id, reason="done")
        assert stop_payload["agent_id"] == agent_id
        assert stop_payload["status"] == "cancelled"
        assert any(item["status"] == "cancelled" for item in stop_payload["notifications"])
        assert coordinator.list_agents()[1]["status"] == "cancelled"
        assert coordinator.list_tasks()[0]["status"] == "skipped"

    asyncio.run(_run())
