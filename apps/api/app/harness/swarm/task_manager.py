from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from app.db.models import TaskNodeStatus, utc_now


@dataclass(slots=True)
class SwarmTaskRecord:
    task_id: str
    agent_id: str
    profile_name: str
    title: str
    status: TaskNodeStatus = TaskNodeStatus.PENDING
    summary: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "profile_name": self.profile_name,
            "title": self.title,
            "status": self.status.value,
            "summary": self.summary,
            "result": dict(self.result),
            "usage": dict(self.usage),
            "metadata": dict(self.metadata),
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class SwarmTaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, SwarmTaskRecord] = {}

    def create_task(
        self,
        *,
        agent_id: str,
        profile_name: str,
        title: str,
        metadata: dict[str, Any] | None = None,
    ) -> SwarmTaskRecord:
        record = SwarmTaskRecord(
            task_id=str(uuid4()),
            agent_id=agent_id,
            profile_name=profile_name,
            title=title,
            metadata=dict(metadata or {}),
        )
        self._tasks[record.task_id] = record
        return record

    def get(self, task_id: str) -> SwarmTaskRecord | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[SwarmTaskRecord]:
        return list(self._tasks.values())

    def start(self, task_id: str) -> SwarmTaskRecord | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.status = TaskNodeStatus.IN_PROGRESS
        task.started_at = utc_now()
        return task

    def complete(
        self,
        task_id: str,
        *,
        summary: str,
        result: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> SwarmTaskRecord | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.status = TaskNodeStatus.COMPLETED
        task.summary = summary
        task.result = dict(result or {})
        task.usage = dict(usage or {})
        task.finished_at = utc_now()
        return task

    def fail(
        self,
        task_id: str,
        *,
        summary: str,
        result: dict[str, Any] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> SwarmTaskRecord | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.status = TaskNodeStatus.FAILED
        task.summary = summary
        task.result = dict(result or {})
        task.usage = dict(usage or {})
        task.finished_at = utc_now()
        return task

    def cancel(self, task_id: str, *, summary: str) -> SwarmTaskRecord | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        task.status = TaskNodeStatus.SKIPPED
        task.summary = summary
        task.finished_at = utc_now()
        return task
