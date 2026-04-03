from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.agent.tool_registry import ToolAccessMode, ToolSpec
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
    access_mode: str | None = None
    side_effect_level: str | None = None
    resource_keys: tuple[str, ...] = ()


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
        access_mode = self._resolve_access_mode(task, tool_spec)
        writes_state = access_mode == ToolAccessMode.WRITE.value
        side_effect_level = self._resolve_side_effect_level(task, tool_spec)
        resource_keys = self._resolve_resource_keys(task, tool_spec)
        return SelectedTask(
            task_id=task.id,
            task_name=task.name,
            stage_key=self._runtime.task_stage(task),
            priority=self._runtime.task_priority(task),
            approval_required=self._runtime.approval_required(task),
            tool_name=tool_spec.name if tool_spec is not None else None,
            writes_state=writes_state,
            scheduler_group=("serialized_write_group" if writes_state else "parallel_read_group"),
            access_mode=access_mode,
            side_effect_level=side_effect_level,
            resource_keys=resource_keys,
        )

    @staticmethod
    def _resolve_access_mode(task: TaskNode, tool_spec: ToolSpec | None) -> str:
        scheduler_access_mode = task.metadata_json.get("scheduler_access_mode")
        if isinstance(scheduler_access_mode, str) and scheduler_access_mode in {
            ToolAccessMode.READ.value,
            ToolAccessMode.WRITE.value,
        }:
            return scheduler_access_mode
        if tool_spec is not None and tool_spec.access_mode is not None:
            return tool_spec.access_mode.value
        if tool_spec is not None and tool_spec.safety_profile.writes_state:
            return ToolAccessMode.WRITE.value
        return ToolAccessMode.READ.value

    @staticmethod
    def _resolve_side_effect_level(task: TaskNode, tool_spec: ToolSpec | None) -> str | None:
        side_effect_level = task.metadata_json.get("scheduler_side_effect_level")
        if isinstance(side_effect_level, str):
            return side_effect_level
        if tool_spec is None:
            return None
        return tool_spec.side_effect_level.value

    @staticmethod
    def _resolve_resource_keys(task: TaskNode, tool_spec: ToolSpec | None) -> tuple[str, ...]:
        resource_keys = task.metadata_json.get("scheduler_resource_keys")
        if isinstance(resource_keys, list):
            return tuple(item for item in resource_keys if isinstance(item, str))
        if tool_spec is not None and tool_spec.resource_keys:
            return tool_spec.resource_keys
        return ()
