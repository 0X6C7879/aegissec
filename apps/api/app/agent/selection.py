from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.agent.workflow import WorkflowGraphRuntime
from app.db.models import TaskNode


@dataclass(frozen=True)
class SelectedTask:
    task_id: str
    task_name: str
    stage_key: str | None
    priority: int
    approval_required: bool


@dataclass(frozen=True)
class RunnableSelection:
    batch_size: int
    selected_tasks: list[SelectedTask]

    @property
    def selected_task_ids(self) -> list[str]:
        return [task.task_id for task in self.selected_tasks]


class RunnableSelector(Protocol):
    def select(self, *, tasks: list[TaskNode], state: dict[str, object]) -> RunnableSelection: ...


class WorkflowRunnableSelector:
    def __init__(self, runtime: WorkflowGraphRuntime | None = None) -> None:
        self._runtime = runtime or WorkflowGraphRuntime()

    def select(self, *, tasks: list[TaskNode], state: dict[str, object]) -> RunnableSelection:
        batch_size = self._runtime.resolve_batch_size(state)
        runnable = self._runtime.pick_runnable_batch(tasks, limit=batch_size)
        return RunnableSelection(
            batch_size=batch_size,
            selected_tasks=[
                SelectedTask(
                    task_id=task.id,
                    task_name=task.name,
                    stage_key=self._runtime.task_stage(task),
                    priority=self._runtime.task_priority(task),
                    approval_required=self._runtime.approval_required(task),
                )
                for task in runnable
            ],
        )
