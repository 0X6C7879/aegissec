from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LoopSelectedTask:
    task_id: str
    task_name: str
    stage_key: str | None
    priority: int
    approval_required: bool
    tool_name: str | None = None
    writes_state: bool = False
    scheduler_group: str | None = None

    def to_state(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "stage_key": self.stage_key,
            "priority": self.priority,
            "approval_required": self.approval_required,
            "tool_name": self.tool_name,
            "writes_state": self.writes_state,
            "scheduler_group": self.scheduler_group,
        }

    @classmethod
    def from_state(cls, raw: object) -> LoopSelectedTask | None:
        if not isinstance(raw, dict):
            return None
        raw_dict = raw
        task_id = raw_dict.get("task_id")
        task_name = raw_dict.get("task_name")
        if not isinstance(task_id, str) or not isinstance(task_name, str):
            return None
        raw_stage_key = raw_dict.get("stage_key")
        stage_key = raw_stage_key if isinstance(raw_stage_key, str) else None
        raw_priority = raw_dict.get("priority")
        priority = raw_priority if isinstance(raw_priority, int) else 0
        approval_required = bool(raw_dict.get("approval_required", False))
        tool_name = (
            raw_dict.get("tool_name") if isinstance(raw_dict.get("tool_name"), str) else None
        )
        writes_state = bool(raw_dict.get("writes_state", False))
        scheduler_group = (
            raw_dict.get("scheduler_group")
            if isinstance(raw_dict.get("scheduler_group"), str)
            else None
        )
        return cls(
            task_id=task_id,
            task_name=task_name,
            stage_key=stage_key,
            priority=priority,
            approval_required=approval_required,
            tool_name=tool_name,
            writes_state=writes_state,
            scheduler_group=scheduler_group,
        )


@dataclass(frozen=True)
class WorkflowCycleArtifact:
    cycle_id: str
    batch_cycle: int
    selected_tasks: list[LoopSelectedTask] = field(default_factory=list)
    parallel_read_group: list[str] = field(default_factory=list)
    serialized_write_group: list[str] = field(default_factory=list)
    scheduler_summary: dict[str, object] = field(default_factory=dict)
    retrieval_summary: str = ""
    retrieval: dict[str, object] = field(default_factory=dict)
    tool_results: list[dict[str, object]] = field(default_factory=list)
    reflection_summary: str = ""
    memory_writes: list[dict[str, object]] = field(default_factory=list)
    memory: dict[str, object] = field(default_factory=dict)
    compaction_summary: dict[str, object] = field(default_factory=dict)
    context_projection: dict[str, object] = field(default_factory=dict)
    next_action: str = "idle"
    started_at: str | None = None
    ended_at: str | None = None

    def to_state(self) -> dict[str, object]:
        return {
            "cycle_id": self.cycle_id,
            "batch_cycle": self.batch_cycle,
            "selected_tasks": [task.to_state() for task in self.selected_tasks],
            "parallel_read_group": list(self.parallel_read_group),
            "serialized_write_group": list(self.serialized_write_group),
            "scheduler_summary": dict(self.scheduler_summary),
            "retrieval_summary": self.retrieval_summary,
            "retrieval": dict(self.retrieval),
            "tool_results": list(self.tool_results),
            "reflection_summary": self.reflection_summary,
            "memory_writes": list(self.memory_writes),
            "memory": dict(self.memory),
            "compaction_summary": dict(self.compaction_summary),
            "context_projection": dict(self.context_projection),
            "next_action": self.next_action,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }

    @classmethod
    def from_state(cls, raw: object) -> WorkflowCycleArtifact | None:
        if not isinstance(raw, dict):
            return None
        raw_dict = raw
        cycle_id = raw_dict.get("cycle_id")
        if not isinstance(cycle_id, str):
            return None
        raw_batch_cycle = raw_dict.get("batch_cycle")
        batch_cycle = raw_batch_cycle if isinstance(raw_batch_cycle, int) else 0
        selected_tasks_raw: list[object] = []
        selected_tasks_value = raw_dict.get("selected_tasks")
        if isinstance(selected_tasks_value, list):
            selected_tasks_raw = selected_tasks_value
        selected_tasks: list[LoopSelectedTask] = []
        for item in selected_tasks_raw:
            parsed_task = LoopSelectedTask.from_state(item)
            if parsed_task is not None:
                selected_tasks.append(parsed_task)
        parallel_read_group = (
            [item for item in raw_dict.get("parallel_read_group", []) if isinstance(item, str)]
            if isinstance(raw_dict.get("parallel_read_group", []), list)
            else []
        )
        serialized_write_group = (
            [item for item in raw_dict.get("serialized_write_group", []) if isinstance(item, str)]
            if isinstance(raw_dict.get("serialized_write_group", []), list)
            else []
        )
        raw_scheduler_summary = raw_dict.get("scheduler_summary")
        scheduler_summary = (
            {str(key): value for key, value in raw_scheduler_summary.items()}
            if isinstance(raw_scheduler_summary, dict)
            else {}
        )
        raw_retrieval = raw_dict.get("retrieval")
        retrieval = (
            {str(key): value for key, value in raw_retrieval.items()}
            if isinstance(raw_retrieval, dict)
            else {}
        )
        tool_results_raw: list[object] = []
        tool_results_value = raw_dict.get("tool_results")
        if isinstance(tool_results_value, list):
            tool_results_raw = tool_results_value
        memory_writes_raw: list[object] = []
        memory_writes_value = raw_dict.get("memory_writes")
        if isinstance(memory_writes_value, list):
            memory_writes_raw = memory_writes_value
        tool_results: list[dict[str, object]] = []
        for item in tool_results_raw:
            if isinstance(item, dict):
                tool_results.append(item)
        memory_writes: list[dict[str, object]] = []
        for item in memory_writes_raw:
            if isinstance(item, dict):
                memory_writes.append(item)
        raw_compaction_summary = raw_dict.get("compaction_summary")
        compaction_summary = (
            {str(key): value for key, value in raw_compaction_summary.items()}
            if isinstance(raw_compaction_summary, dict)
            else {}
        )
        raw_memory = raw_dict.get("memory")
        memory = (
            {str(key): value for key, value in raw_memory.items()}
            if isinstance(raw_memory, dict)
            else {}
        )
        raw_context_projection = raw_dict.get("context_projection")
        context_projection = (
            {str(key): value for key, value in raw_context_projection.items()}
            if isinstance(raw_context_projection, dict)
            else {}
        )
        return cls(
            cycle_id=cycle_id,
            batch_cycle=batch_cycle,
            selected_tasks=selected_tasks,
            parallel_read_group=parallel_read_group,
            serialized_write_group=serialized_write_group,
            scheduler_summary=scheduler_summary,
            retrieval_summary=str(raw_dict.get("retrieval_summary") or ""),
            retrieval=retrieval,
            tool_results=tool_results,
            reflection_summary=str(raw_dict.get("reflection_summary") or ""),
            memory_writes=memory_writes,
            memory=memory,
            compaction_summary=compaction_summary,
            context_projection=context_projection,
            next_action=str(raw_dict.get("next_action") or "idle"),
            started_at=(
                raw_dict.get("started_at") if isinstance(raw_dict.get("started_at"), str) else None
            ),
            ended_at=raw_dict.get("ended_at")
            if isinstance(raw_dict.get("ended_at"), str)
            else None,
        )


@dataclass(frozen=True)
class WorkflowLoopState:
    cycles: list[WorkflowCycleArtifact] = field(default_factory=list)
    current_cycle_id: str | None = None

    def to_state(self) -> dict[str, object]:
        return {
            "current_cycle_id": self.current_cycle_id,
            "cycles": [cycle.to_state() for cycle in self.cycles],
        }

    def apply_to_state(self, state: dict[str, object]) -> None:
        state["loop"] = self.to_state()

    @classmethod
    def empty(cls) -> WorkflowLoopState:
        return cls(cycles=[], current_cycle_id=None)

    @classmethod
    def from_state(cls, state: dict[str, object]) -> WorkflowLoopState:
        raw = state.get("loop")
        if not isinstance(raw, dict):
            return cls.empty()
        raw_dict = raw
        cycles_raw: list[object] = []
        cycles_value = raw_dict.get("cycles")
        if isinstance(cycles_value, list):
            cycles_raw = cycles_value
        cycles: list[WorkflowCycleArtifact] = []
        for item in cycles_raw:
            parsed_cycle = WorkflowCycleArtifact.from_state(item)
            if parsed_cycle is not None:
                cycles.append(parsed_cycle)
        current_cycle_id = (
            raw_dict.get("current_cycle_id")
            if isinstance(raw_dict.get("current_cycle_id"), str)
            else None
        )
        return cls(cycles=cycles, current_cycle_id=current_cycle_id)

    def append_cycle(self, cycle: WorkflowCycleArtifact) -> WorkflowLoopState:
        return WorkflowLoopState(
            cycles=[*self.cycles, cycle],
            current_cycle_id=cycle.cycle_id,
        )
