from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from app.db.models import MessageRole, TaskNodeStatus

from .agent_profiles import SwarmAgentProfile, build_default_agent_profiles
from .in_process_backend import InProcessAgentContext, InProcessSwarmBackend
from .mailbox import MailboxMessageKind, SwarmMailbox, create_shutdown_request, create_user_message
from .notifications import SwarmNotification, SwarmNotificationStatus
from .registry import SwarmAgentRecord, SwarmAgentStatus, SwarmRegistry
from .task_manager import SwarmTaskManager


class InProcessSwarmCoordinator:
    def __init__(
        self,
        *,
        session_id: str,
        session_state: Any | None = None,
        session: Any | None = None,
        chat_runtime: Any | None = None,
        runtime_service: Any | None = None,
        skill_service: Any | None = None,
        mcp_service: Any | None = None,
        profiles: dict[str, SwarmAgentProfile] | None = None,
        registry: SwarmRegistry | None = None,
        mailbox: SwarmMailbox | None = None,
        task_manager: SwarmTaskManager | None = None,
        backend: InProcessSwarmBackend | None = None,
    ) -> None:
        self.session_id = session_id
        self.session_state = session_state
        self._session = session
        self._chat_runtime = chat_runtime
        self._runtime_service = runtime_service
        self._skill_service = skill_service
        self._mcp_service = mcp_service
        self._profiles = profiles or build_default_agent_profiles()
        self._registry = registry or SwarmRegistry()
        self._mailbox = mailbox or SwarmMailbox()
        self._task_manager = task_manager or SwarmTaskManager()
        self._backend = backend or InProcessSwarmBackend()
        self._notifications: list[SwarmNotification] = []
        self._coordinator_agent_id: str | None = None
        self._agent_histories: dict[str, list[Any]] = {}
        self._agent_session_states: dict[str, Any | None] = {}

    @staticmethod
    def _normalize_mapping(value: object) -> dict[str, Any] | None:
        return dict(value) if isinstance(value, dict) else None

    @staticmethod
    def _normalize_string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]

    @staticmethod
    def _normalize_object_list(value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                normalized.append(dict(item))
        return normalized

    def ensure_primary_agent(
        self, *, objective: str, metadata: dict[str, Any] | None = None
    ) -> str:
        if self._coordinator_agent_id is not None:
            return self._coordinator_agent_id
        agent_id = f"coordinator-{uuid4()}"
        self._registry.register(
            SwarmAgentRecord(
                agent_id=agent_id,
                profile_name="coordinator",
                parent_agent_id=None,
                objective=objective,
                status=SwarmAgentStatus.RUNNING,
                metadata=dict(metadata or {}),
            )
        )
        self._coordinator_agent_id = agent_id
        self._emit(
            SwarmNotification(
                agent_id=agent_id,
                status=SwarmNotificationStatus.STARTED,
                summary="Coordinator initialized for in-process swarm runtime.",
                metadata={"profile_name": "coordinator"},
            )
        )
        if self.session_state is not None and hasattr(self.session_state, "swarm"):
            self.session_state.swarm["coordinator_agent_id"] = agent_id
        return agent_id

    def profiles(self) -> list[SwarmAgentProfile]:
        return list(self._profiles.values())

    def list_agents(self) -> list[dict[str, Any]]:
        return [
            {
                "agent_id": agent.agent_id,
                "profile_name": agent.profile_name,
                "status": agent.status.value,
                "objective": agent.objective,
                "parent_agent_id": agent.parent_agent_id,
                "metadata": dict(agent.metadata),
            }
            for agent in self._registry.list_agents()
        ]

    def list_tasks(self) -> list[dict[str, Any]]:
        return [task.as_payload() for task in self._task_manager.list_tasks()]

    async def spawn_agent(
        self,
        *,
        profile_name: str,
        objective: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        coordinator_agent_id = self.ensure_primary_agent(objective=objective)
        profile = self._profiles.get(profile_name)
        if profile is None:
            raise ValueError(f"Unknown swarm profile: {profile_name}")
        agent_id = f"{profile_name}-{uuid4()}"
        agent = self._registry.register(
            SwarmAgentRecord(
                agent_id=agent_id,
                profile_name=profile.name,
                parent_agent_id=coordinator_agent_id,
                objective=objective,
                status=SwarmAgentStatus.CREATED,
                metadata=dict(metadata or {}),
            )
        )
        task = self._task_manager.create_task(
            agent_id=agent_id,
            profile_name=profile.name,
            title=objective,
            metadata={"role": profile.role, **dict(metadata or {})},
        )
        self._agent_histories[agent_id] = []
        self._agent_session_states[agent_id] = (
            copy.deepcopy(self.session_state) if self.session_state is not None else None
        )
        self._mailbox.ensure_queue(agent_id)
        self._backend.start(
            context=InProcessAgentContext(
                agent_id=agent_id,
                profile_name=profile.name,
                session_id=self.session_id,
                objective=objective,
                task_id=task.task_id,
                metadata=dict(metadata or {}),
            ),
            runner=self._build_agent_runner(agent_id=agent_id, task_id=task.task_id),
        )
        self._emit(
            SwarmNotification(
                agent_id=agent_id,
                status=SwarmNotificationStatus.PLANNED,
                summary=f"Spawned {profile.name} for objective: {objective}",
                task_id=task.task_id,
                metadata={"profile_name": profile.name, **dict(metadata or {})},
            )
        )
        await asyncio.sleep(0)
        if self.session_state is not None and hasattr(self.session_state, "swarm"):
            self.session_state.swarm.setdefault("agent_ids", []).append(agent_id)
        return {
            "agent": {
                "agent_id": agent.agent_id,
                "profile_name": agent.profile_name,
                "status": agent.status.value,
                "objective": agent.objective,
                "parent_agent_id": agent.parent_agent_id,
                "metadata": dict(agent.metadata),
            },
            "task": task.as_payload(),
            "notifications": self.drain_notifications(),
        }

    async def send_message(
        self,
        *,
        agent_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        coordinator_agent_id = self.ensure_primary_agent(objective=content)
        agent = self._registry.get(agent_id)
        if agent is None:
            raise ValueError(f"Unknown swarm agent: {agent_id}")
        message = await self._mailbox.send(
            create_user_message(
                sender_id=coordinator_agent_id,
                recipient_id=agent_id,
                content=content,
                metadata=dict(metadata or {}),
            )
        )
        self._emit(
            SwarmNotification(
                agent_id=agent_id,
                status=SwarmNotificationStatus.MESSAGE,
                summary=f"Delivered message to {agent.profile_name}.",
                metadata={"message_id": message.message_id, **dict(metadata or {})},
            )
        )
        return {
            "agent_id": agent_id,
            "message": message.as_payload(),
            "notifications": self.drain_notifications(),
        }

    async def stop_agent(
        self,
        *,
        agent_id: str,
        reason: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        agent = self._registry.get(agent_id)
        if agent is None:
            raise ValueError(f"Unknown swarm agent: {agent_id}")
        coordinator_agent_id = self.ensure_primary_agent(objective=reason or "stop")
        await self._mailbox.send(
            create_shutdown_request(
                sender_id=coordinator_agent_id,
                recipient_id=agent_id,
                reason=reason,
            )
        )
        await self._backend.stop(agent_id, force=force)
        self._registry.update_status(agent_id, SwarmAgentStatus.CANCELLED)
        for task in self._task_manager.list_tasks():
            if task.agent_id == agent_id and task.finished_at is None:
                self._task_manager.cancel(task.task_id, summary=reason or "Agent stopped")
        self._emit(
            SwarmNotification(
                agent_id=agent_id,
                status=SwarmNotificationStatus.CANCELLED,
                summary=reason or f"Stopped agent {agent_id}.",
                metadata={"force": force},
            )
        )
        return {
            "agent_id": agent_id,
            "status": SwarmAgentStatus.CANCELLED.value,
            "notifications": self.drain_notifications(),
        }

    def drain_notifications(self) -> list[dict[str, Any]]:
        notifications = [item.as_payload() for item in self._notifications]
        self._notifications.clear()
        return notifications

    def _emit(self, notification: SwarmNotification) -> None:
        self._notifications.append(notification)

    def _build_agent_runner(
        self, *, agent_id: str, task_id: str
    ) -> Callable[[InProcessAgentContext], Awaitable[dict[str, Any] | None]]:
        async def _runner(context: InProcessAgentContext) -> dict[str, Any] | None:
            self._registry.update_status(agent_id, SwarmAgentStatus.RUNNING)
            self._task_manager.start(task_id)
            self._emit(
                SwarmNotification(
                    agent_id=agent_id,
                    status=SwarmNotificationStatus.STARTED,
                    summary=f"{context.profile_name} is running.",
                    task_id=task_id,
                    metadata={"profile_name": context.profile_name},
                )
            )
            last_message: dict[str, Any] | None = None
            try:
                while not context.cancel_event.is_set():
                    message = await self._mailbox.receive(agent_id, timeout_seconds=0.05)
                    if message is None:
                        continue
                    if message.kind == MailboxMessageKind.SHUTDOWN:
                        self._registry.update_status(agent_id, SwarmAgentStatus.CANCELLED)
                        return {"shutdown": True, "reason": message.payload.get("reason")}
                    if message.kind == MailboxMessageKind.USER_MESSAGE:
                        last_message = message.as_payload()
                        self._emit(
                            SwarmNotification(
                                agent_id=agent_id,
                                status=SwarmNotificationStatus.MESSAGE,
                                summary=f"{context.profile_name} received a message.",
                                task_id=task_id,
                                metadata={"message_id": message.message_id},
                            )
                        )
                        if self._chat_runtime is None:
                            self._registry.update_status(agent_id, SwarmAgentStatus.IDLE)
                            continue
                        try:
                            result = await self._execute_agent_message(
                                agent_id=agent_id,
                                context=context,
                                task_id=task_id,
                                content=str(message.payload.get("content") or ""),
                            )
                        except Exception as exc:
                            self._registry.update_status(agent_id, SwarmAgentStatus.FAILED)
                            self._task_manager.fail(task_id, summary=str(exc))
                            self._emit(
                                SwarmNotification(
                                    agent_id=agent_id,
                                    status=SwarmNotificationStatus.FAILED,
                                    summary=str(exc),
                                    task_id=task_id,
                                    reason=str(exc),
                                )
                            )
                            return {"error": str(exc)}

                        self._registry.update_status(agent_id, SwarmAgentStatus.COMPLETED)
                        usage_payload = self._normalize_mapping(result.get("usage"))
                        evidence_ids = self._normalize_string_list(result.get("evidence_ids"))
                        hypothesis_ids = self._normalize_string_list(result.get("hypothesis_ids"))
                        graph_updates = self._normalize_object_list(result.get("graph_updates"))
                        artifacts = self._normalize_string_list(result.get("artifacts"))
                        reason = (
                            str(result.get("reason"))
                            if isinstance(result.get("reason"), str)
                            else None
                        )
                        completed_task = self._task_manager.complete(
                            task_id,
                            summary=str(result.get("content") or "Agent execution completed."),
                            result=result,
                            usage=usage_payload,
                        )
                        if completed_task is not None:
                            completed_task.metadata["profile_name"] = context.profile_name
                        self._emit(
                            SwarmNotification(
                                agent_id=agent_id,
                                status=SwarmNotificationStatus.COMPLETED,
                                summary=str(result.get("content") or "Agent execution completed."),
                                task_id=task_id,
                                result=result,
                                usage=usage_payload or {},
                                evidence_ids=evidence_ids,
                                hypothesis_ids=hypothesis_ids,
                                graph_updates=graph_updates,
                                artifacts=artifacts,
                                reason=reason,
                                metadata={"profile_name": context.profile_name},
                            )
                        )
                        return result
                self._registry.update_status(agent_id, SwarmAgentStatus.CANCELLED)
                return {"cancelled": True}
            finally:
                agent_record = self._registry.get(agent_id)
                if agent_record is not None and agent_record.status not in {
                    SwarmAgentStatus.CANCELLED,
                    SwarmAgentStatus.COMPLETED,
                    SwarmAgentStatus.FAILED,
                }:
                    self._registry.update_status(agent_id, SwarmAgentStatus.IDLE)
                task = self._task_manager.get(task_id)
                if (
                    task is not None
                    and task.finished_at is None
                    and task.status == TaskNodeStatus.IN_PROGRESS
                ):
                    task.summary = "Agent waiting for additional work."
                if last_message is not None:
                    task = self._task_manager.get(task_id)
                    if task is not None:
                        task.metadata["last_message"] = last_message

        return _runner

    async def _execute_agent_message(
        self,
        *,
        agent_id: str,
        context: InProcessAgentContext,
        task_id: str,
        content: str,
    ) -> dict[str, Any]:
        if self._chat_runtime is None:
            raise RuntimeError("Swarm agent runtime is not configured.")

        executor_module = __import__("app.harness.executor", fromlist=["*"])
        messages_module = __import__(
            "app.harness.messages", fromlist=["ConversationMessage", "ToolCallResult"]
        )
        scheduling_module = __import__("app.harness.tool_scheduling", fromlist=["*"])
        semantic_module = __import__(
            "app.harness.semantic",
            fromlist=[
                "clear_pending_semantic_deltas",
                "semantic_snapshot_from_state",
                "stage_semantic_deltas",
            ],
        )
        session_runner_module = __import__("app.harness.session_runner", fromlist=["*"])
        capabilities_module = __import__("app.services.capabilities", fromlist=["CapabilityFacade"])
        ConversationMessage = messages_module.ConversationMessage
        ToolCallResult = messages_module.ToolCallResult
        clear_pending_semantic_deltas = semantic_module.clear_pending_semantic_deltas
        semantic_snapshot_from_state = semantic_module.semantic_snapshot_from_state
        stage_semantic_deltas = semantic_module.stage_semantic_deltas

        capability_facade = None
        mcp_tool_inventory: list[dict[str, Any]] = []
        if self._skill_service is not None and self._mcp_service is not None:
            capability_facade = capabilities_module.CapabilityFacade(
                skill_service=self._skill_service,
                mcp_service=self._mcp_service,
            )
            mcp_tool_inventory = capability_facade.build_mcp_tool_inventory()

        tool_runtime = executor_module.build_tool_runtime(
            skill_service=self._skill_service,
            session_id=self.session_id,
            mcp_tool_inventory=mcp_tool_inventory,
            include_swarm_tools=False,
        )
        conversation_messages = self._agent_histories.setdefault(agent_id, [])
        agent_session_state = self._agent_session_states.get(agent_id)
        session_object = self._session or SimpleNamespace(
            id=self.session_id,
            runtime_policy_json={},
        )
        assistant_message = SimpleNamespace(id=f"{agent_id}-assistant", generation_id=task_id)

        async def execute_tool(tool_request: Any) -> Any:
            prepared = executor_module.prepare_tool_execution(
                runtime=tool_runtime,
                tool_request=tool_request,
                session=session_object,
                assistant_message=assistant_message,
                runtime_service=self._runtime_service,
                skill_service=self._skill_service,
                mcp_service=self._mcp_service,
                session_state=agent_session_state,
                swarm_coordinator=None,
            )
            if prepared.tool is None:
                raise RuntimeError(f"Unsupported tool requested: {tool_request.tool_name}.")
            if prepared.decision is None or not prepared.decision.allowed:
                raise RuntimeError(prepared.decision.reason if prepared.decision else "Tool denied")
            try:
                tool_result = await executor_module.run_tool_with_hooks(
                    runtime=tool_runtime,
                    prepared=prepared,
                    tool_request=tool_request,
                )
            except Exception as exc:
                await executor_module.notify_tool_execution_error(
                    runtime=tool_runtime,
                    prepared=prepared,
                    tool_request=tool_request,
                    error=exc,
                )
                raise
            if agent_session_state is not None and getattr(tool_result, "semantic_deltas", None):
                stage_semantic_deltas(
                    agent_session_state,
                    tool_result.semantic_deltas,
                )
            return ToolCallResult(tool_name=tool_request.tool_name, payload=tool_result.payload)

        async def batch_execute(tool_requests: list[Any]) -> list[Any]:
            prepared_executions = [
                executor_module.prepare_tool_execution(
                    runtime=tool_runtime,
                    tool_request=tool_request,
                    session=session_object,
                    assistant_message=assistant_message,
                    runtime_service=self._runtime_service,
                    skill_service=self._skill_service,
                    mcp_service=self._mcp_service,
                    session_state=agent_session_state,
                    swarm_coordinator=None,
                )
                for tool_request in tool_requests
            ]
            phases = scheduling_module.build_tool_schedule(tool_requests, prepared_executions)
            ordered_results: list[Any | None] = [None] * len(tool_requests)
            for phase in phases:
                if phase.lane == "readonly_parallel" and len(phase.items) > 1:

                    async def run_parallel(item: Any) -> tuple[int, Any]:
                        result = await execute_tool(item.tool_request)
                        return item.order, result

                    resolved = await asyncio.gather(*(run_parallel(item) for item in phase.items))
                    for order, result in resolved:
                        ordered_results[order] = result
                    continue
                for item in phase.items:
                    ordered_results[item.order] = await execute_tool(item.tool_request)
            return [result for result in ordered_results if result is not None]

        setattr(execute_tool, "__batch_execute__", batch_execute)

        available_skills = tool_runtime.available_skills
        skill_context_prompt = (
            f"Role: {context.profile_name}\n"
            f"Title: {self._profiles[context.profile_name].title}\n"
            f"Instructions: {self._profiles[context.profile_name].instructions}\n"
            f"Objective: {context.objective}"
        )
        generate_reply_kwargs: dict[str, Any] = {
            "conversation_messages": list(conversation_messages),
            "available_skills": available_skills,
            "skill_context_prompt": skill_context_prompt,
            "execute_tool": execute_tool,
        }
        if session_runner_module.chat_runtime_supports_mcp_tools(self._chat_runtime):
            generate_reply_kwargs["mcp_tools"] = mcp_tool_inventory
        if session_runner_module.chat_runtime_supports_harness_state(self._chat_runtime):
            generate_reply_kwargs["harness_state"] = agent_session_state

        reply = await self._chat_runtime.generate_reply(content, [], **generate_reply_kwargs)
        conversation_messages.append(ConversationMessage(role=MessageRole.USER, content=content))
        conversation_messages.append(ConversationMessage(role=MessageRole.ASSISTANT, content=reply))

        semantic_snapshot = (
            semantic_snapshot_from_state(agent_session_state)
            if agent_session_state is not None
            else {}
        )
        if agent_session_state is not None:
            clear_pending_semantic_deltas(agent_session_state)

        return {
            "content": reply,
            "evidence_ids": self._normalize_string_list(semantic_snapshot.get("evidence_ids")),
            "hypothesis_ids": self._normalize_string_list(semantic_snapshot.get("hypothesis_ids")),
            "graph_updates": self._normalize_object_list(semantic_snapshot.get("graph_updates")),
            "artifacts": self._normalize_string_list(semantic_snapshot.get("artifacts")),
            "reason": (
                str(semantic_snapshot.get("reason"))
                if isinstance(semantic_snapshot.get("reason"), str)
                else None
            ),
        }


def build_default_swarm_coordinator(
    *,
    session_id: str,
    session_state: Any | None = None,
    session: Any | None = None,
    chat_runtime: Any | None = None,
    runtime_service: Any | None = None,
    skill_service: Any | None = None,
    mcp_service: Any | None = None,
) -> InProcessSwarmCoordinator:
    return InProcessSwarmCoordinator(
        session_id=session_id,
        session_state=session_state,
        session=session,
        chat_runtime=chat_runtime,
        runtime_service=runtime_service,
        skill_service=skill_service,
        mcp_service=mcp_service,
    )
