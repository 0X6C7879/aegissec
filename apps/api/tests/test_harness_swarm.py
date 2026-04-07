import asyncio
import importlib
from types import SimpleNamespace
from typing import Any

from app.services.chat_runtime import ToolCallRequest

HarnessSessionState = importlib.import_module("app.harness.state").HarnessSessionState
build_default_swarm_coordinator = importlib.import_module(
    "app.harness.swarm"
).build_default_swarm_coordinator


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


def test_swarm_coordinator_can_complete_in_memory_query_loop() -> None:
    class _FakeSkillService:
        def list_loaded_skills_for_agent(self, *, session_id: str) -> list[object]:
            return []

    class _FakeMCPService:
        def list_servers(self) -> list[object]:
            return []

    class _ToolCallingRuntime:
        async def generate_reply(
            self,
            content: str,
            attachments: list[object],
            *,
            conversation_messages: list[object] | None = None,
            available_skills: list[object] | None = None,
            mcp_tools: list[object] | None = None,
            skill_context_prompt: str | None = None,
            execute_tool: Any | None = None,
            callbacks: Any | None = None,
            harness_state: Any | None = None,
        ) -> str:
            assert conversation_messages == []
            assert available_skills == []
            assert mcp_tools == []
            assert isinstance(skill_context_prompt, str)
            assert harness_state is not None
            assert execute_tool is not None
            tool_result = await execute_tool(
                ToolCallRequest(
                    tool_call_id="subagent-tool-1",
                    tool_name="list_available_skills",
                    arguments={},
                )
            )
            assert tool_result.payload["skills"] == []
            return f"worker:{content}:0"

    async def _run() -> None:
        session_state = HarnessSessionState(session_id="sess-2", memory_key="sess-2")
        coordinator = build_default_swarm_coordinator(
            session_id="sess-2",
            session_state=session_state,
            session=SimpleNamespace(id="sess-2", runtime_policy_json={}),
            chat_runtime=_ToolCallingRuntime(),
            runtime_service=SimpleNamespace(),
            skill_service=_FakeSkillService(),
            mcp_service=_FakeMCPService(),
        )

        spawn_payload = await coordinator.spawn_agent(
            profile_name="planner_agent",
            objective="Plan the next attack path.",
        )
        agent_id = spawn_payload["agent"]["agent_id"]
        task_id = spawn_payload["task"]["task_id"]

        message_payload = await coordinator.send_message(
            agent_id=agent_id,
            content="Validate the plan assumptions.",
        )
        assert any(item["status"] == "message" for item in message_payload["notifications"])

        await asyncio.sleep(0.1)
        notifications = coordinator.drain_notifications()
        completed = [item for item in notifications if item["status"] == "completed"]
        assert completed
        completed_payload = completed[0]
        assert completed_payload["agent_id"] == agent_id
        assert completed_payload["task_id"] == task_id
        assert completed_payload["result"]["content"] == "worker:Validate the plan assumptions.:0"
        assert completed_payload["evidence_ids"] == []
        assert completed_payload["hypothesis_ids"] == []
        assert completed_payload["artifacts"] == []

        task_payload = coordinator.list_tasks()[0]
        assert task_payload["status"] == "completed"
        assert task_payload["result"]["content"] == "worker:Validate the plan assumptions.:0"
        assert coordinator.list_agents()[1]["status"] == "completed"

    asyncio.run(_run())
