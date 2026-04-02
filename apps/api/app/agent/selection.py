from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.agent.tool_registry import ToolSpec
from app.agent.workflow import WorkflowGraphRuntime
from app.db.models import TaskNode


@dataclass(frozen=True)
class SelectedTask:
    task_id: str
    task_name: str
    stage_key: str | None
    priority: int
    approval_required: bool
    tool_name: str | None = None
    writes_state: bool = False
    scheduler_group: str | None = None


@dataclass(frozen=True)
class RunnableSelection:
    batch_size: int
    selected_tasks: list[SelectedTask]
    parallel_read_group: list[SelectedTask] = field(default_factory=list)
    serialized_write_group: list[SelectedTask] = field(default_factory=list)

    @property
    def selected_task_ids(self) -> list[str]:
        return [task.task_id for task in self.selected_tasks]


class RunnableSelector(Protocol):
    def select(self, *, tasks: list[TaskNode], state: dict[str, object]) -> RunnableSelection: ...


class ToolSpecResolver(Protocol):
    def __call__(self, task: TaskNode) -> ToolSpec: ...


class WorkflowRunnableSelector:
    def __init__(
        self,
        runtime: WorkflowGraphRuntime | None = None,
        tool_spec_resolver: ToolSpecResolver | None = None,
    ) -> None:
        self._runtime = runtime or WorkflowGraphRuntime()
        self._tool_spec_resolver = tool_spec_resolver

    def select(self, *, tasks: list[TaskNode], state: dict[str, object]) -> RunnableSelection:
        batch_size = self._runtime.resolve_batch_size(state)
        runnable = self._runtime.pick_runnable_batch(tasks, limit=batch_size)
        selected_tasks = [self._build_selected_task(task) for task in runnable]
        parallel_read_group = [task for task in selected_tasks if not task.writes_state]
        serialized_write_group = [task for task in selected_tasks if task.writes_state]
        return RunnableSelection(
            batch_size=batch_size,
            selected_tasks=selected_tasks,
            parallel_read_group=parallel_read_group,
            serialized_write_group=serialized_write_group,
        )

    def _build_selected_task(self, task: TaskNode) -> SelectedTask:
        tool_spec = self._tool_spec_resolver(task) if self._tool_spec_resolver is not None else None
        writes_state = tool_spec.safety_profile.writes_state if tool_spec is not None else False
        return SelectedTask(
            task_id=task.id,
            task_name=task.name,
            stage_key=self._runtime.task_stage(task),
            priority=self._runtime.task_priority(task),
            approval_required=self._runtime.approval_required(task),
            tool_name=tool_spec.name if tool_spec is not None else None,
            writes_state=writes_state,
            scheduler_group=("serialized_write_group" if writes_state else "parallel_read_group"),
        )
