from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.db.models import TaskNode, TaskNodeStatus, TaskNodeType, WorkflowRunStatus

_TERMINAL_TASK_STATUSES = frozenset(
    {TaskNodeStatus.COMPLETED, TaskNodeStatus.FAILED, TaskNodeStatus.SKIPPED}
)


class WorkflowNodeExecutionState(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class PlannedTaskNode:
    planner_key: str
    name: str
    node_type: TaskNodeType
    sequence: int
    stage_key: str
    role: str
    title: str
    description: str
    depends_on: tuple[str, ...] = ()
    parent_key: str | None = None
    priority: int = 50
    approval_required: bool = False
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowExecutionContext:
    session_id: str
    workflow_run_id: str
    goal: str
    template_name: str
    current_stage: str | None
    runtime_policy: dict[str, object]
    project_id: str | None = None
    retrieval: dict[str, object] = field(default_factory=dict)
    memory: dict[str, object] = field(default_factory=dict)
    context_projection: dict[str, object] = field(default_factory=dict)
    prompting: dict[str, object] = field(default_factory=dict)


class WorkflowGraphRuntime:
    DEFAULT_BATCH_SIZE = 3

    @staticmethod
    def execution_state_for(task: TaskNode) -> WorkflowNodeExecutionState:
        if task.status is TaskNodeStatus.READY:
            return WorkflowNodeExecutionState.QUEUED
        if task.status is TaskNodeStatus.IN_PROGRESS:
            return WorkflowNodeExecutionState.RUNNING
        if task.status is TaskNodeStatus.BLOCKED and WorkflowGraphRuntime.approval_required(task):
            return WorkflowNodeExecutionState.WAITING_APPROVAL
        if task.status is TaskNodeStatus.COMPLETED:
            return WorkflowNodeExecutionState.SUCCESS
        if task.status is TaskNodeStatus.FAILED:
            return WorkflowNodeExecutionState.FAILED
        if task.status is TaskNodeStatus.SKIPPED:
            return WorkflowNodeExecutionState.SKIPPED
        return WorkflowNodeExecutionState.PENDING

    @classmethod
    def sync_execution_state(cls, task: TaskNode) -> None:
        task.metadata_json = {
            **dict(task.metadata_json),
            "execution_state": cls.execution_state_for(task).value,
        }

    @staticmethod
    def is_terminal_task(task: TaskNode) -> bool:
        return task.status in _TERMINAL_TASK_STATUSES

    @staticmethod
    def task_depends_on_ids(task: TaskNode) -> list[str]:
        depends_on = task.metadata_json.get("depends_on_task_ids", [])
        if not isinstance(depends_on, list):
            return []
        return [dependency for dependency in depends_on if isinstance(dependency, str)]

    @staticmethod
    def approval_required(task: TaskNode) -> bool:
        return bool(task.metadata_json.get("approval_required", False))

    @staticmethod
    def task_priority(task: TaskNode) -> int:
        priority = task.metadata_json.get("priority", 0)
        if isinstance(priority, int):
            return priority
        return 0

    @staticmethod
    def sibling_priority_rank(task: TaskNode) -> int | None:
        rank = task.metadata_json.get("sibling_priority_rank")
        if isinstance(rank, int) and rank >= 0:
            return rank
        return None

    @staticmethod
    def task_stage(task: TaskNode) -> str | None:
        stage_key = task.metadata_json.get("stage_key")
        if isinstance(stage_key, str):
            return stage_key
        return None

    @classmethod
    def materialize_ready_tasks(cls, tasks: list[TaskNode]) -> list[TaskNode]:
        task_index = {task.id: task for task in tasks}
        updated: list[TaskNode] = []
        for task in tasks:
            if task.status not in {TaskNodeStatus.PENDING, TaskNodeStatus.BLOCKED}:
                continue
            if task.status is TaskNodeStatus.BLOCKED and cls.approval_required(task):
                continue
            dependencies = cls.task_depends_on_ids(task)
            if dependencies and not all(
                task_index.get(dependency) is not None
                and task_index[dependency].status is TaskNodeStatus.COMPLETED
                for dependency in dependencies
            ):
                continue
            task.status = TaskNodeStatus.READY
            cls.sync_execution_state(task)
            updated.append(task)
        return updated

    @classmethod
    def pick_runnable_task(cls, tasks: list[TaskNode]) -> TaskNode | None:
        batch = cls.pick_runnable_batch(tasks, limit=1)
        return batch[0] if batch else None

    @classmethod
    def pick_runnable_batch(cls, tasks: list[TaskNode], *, limit: int) -> list[TaskNode]:
        runnable = [
            task
            for task in tasks
            if task.status is TaskNodeStatus.READY
            or (
                task.status is TaskNodeStatus.PENDING
                and not cls.task_depends_on_ids(task)
                and not cls.approval_required(task)
            )
        ]
        if not runnable:
            return []
        runnable.sort(
            key=lambda task: (
                0 if cls.sibling_priority_rank(task) is not None else 1,
                (
                    cls.sibling_priority_rank(task)
                    if cls.sibling_priority_rank(task) is not None
                    else task.sequence
                ),
                task.sequence,
                -cls.task_priority(task),
                task.created_at,
                task.id,
            )
        )
        return runnable[: max(limit, 0)]

    @classmethod
    def resolve_batch_size(cls, state: dict[str, object]) -> int:
        batch = state.get("batch", {})
        if isinstance(batch, dict):
            value = batch.get("max_nodes_per_cycle")
            if isinstance(value, int) and value > 0:
                return value
        return cls.DEFAULT_BATCH_SIZE

    @classmethod
    def blocked_for_approval(cls, tasks: list[TaskNode]) -> list[TaskNode]:
        blocked = [
            task
            for task in tasks
            if task.status is TaskNodeStatus.BLOCKED and cls.approval_required(task)
        ]
        blocked.sort(key=lambda task: (task.sequence, task.created_at, task.id))
        return blocked

    @classmethod
    def resolve_run_status(cls, tasks: list[TaskNode]) -> WorkflowRunStatus:
        if any(task.status is TaskNodeStatus.FAILED for task in tasks):
            return WorkflowRunStatus.ERROR
        if cls.blocked_for_approval(tasks):
            return WorkflowRunStatus.NEEDS_APPROVAL
        if all(cls.is_terminal_task(task) for task in tasks):
            return WorkflowRunStatus.DONE
        return WorkflowRunStatus.RUNNING
