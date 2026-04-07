from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from app.db.models import TaskNodeStatus

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
        profiles: dict[str, SwarmAgentProfile] | None = None,
        registry: SwarmRegistry | None = None,
        mailbox: SwarmMailbox | None = None,
        task_manager: SwarmTaskManager | None = None,
        backend: InProcessSwarmBackend | None = None,
    ) -> None:
        self.session_id = session_id
        self.session_state = session_state
        self._profiles = profiles or build_default_agent_profiles()
        self._registry = registry or SwarmRegistry()
        self._mailbox = mailbox or SwarmMailbox()
        self._task_manager = task_manager or SwarmTaskManager()
        self._backend = backend or InProcessSwarmBackend()
        self._notifications: list[SwarmNotification] = []
        self._coordinator_agent_id: str | None = None

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
        await self._mailbox.send(
            create_user_message(
                sender_id=coordinator_agent_id,
                recipient_id=agent_id,
                content=objective,
                metadata=dict(metadata or {}),
            )
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
                        self._registry.update_status(agent_id, SwarmAgentStatus.IDLE)
                        self._emit(
                            SwarmNotification(
                                agent_id=agent_id,
                                status=SwarmNotificationStatus.MESSAGE,
                                summary=f"{context.profile_name} received a message.",
                                task_id=task_id,
                                metadata={"message_id": message.message_id},
                            )
                        )
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


def build_default_swarm_coordinator(
    *, session_id: str, session_state: Any | None = None
) -> InProcessSwarmCoordinator:
    return InProcessSwarmCoordinator(session_id=session_id, session_state=session_state)
