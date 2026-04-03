from __future__ import annotations

from dataclasses import dataclass, field

from app.agent.selection import RunnableSelection, SelectedTask

SCHEDULER_MODE = "phase3_read_parallel_write_serial"


@dataclass(frozen=True)
class ScheduledTaskPhase:
    scheduler_group: str
    tasks: list[SelectedTask] = field(default_factory=list)

    @property
    def task_ids(self) -> list[str]:
        return [task.task_id for task in self.tasks]


@dataclass(frozen=True)
class WorkflowToolSchedule:
    scheduler_mode: str
    selected_tasks: list[SelectedTask]
    parallel_read_group: list[SelectedTask]
    serialized_write_group: list[SelectedTask]
    phases: list[ScheduledTaskPhase]


class WorkflowToolScheduler:
    def build_schedule(self, selection: RunnableSelection) -> WorkflowToolSchedule:
        phases: list[ScheduledTaskPhase] = []
        pending_parallel: list[SelectedTask] = []
        for task in selection.selected_tasks:
            if task.scheduler_group == "parallel_read_group":
                pending_parallel.append(task)
                continue
            if pending_parallel:
                phases.append(
                    ScheduledTaskPhase(
                        scheduler_group="parallel_read_group",
                        tasks=list(pending_parallel),
                    )
                )
                pending_parallel = []
            phases.append(
                ScheduledTaskPhase(
                    scheduler_group="serialized_write_group",
                    tasks=[task],
                )
            )
        if pending_parallel:
            phases.append(
                ScheduledTaskPhase(
                    scheduler_group="parallel_read_group",
                    tasks=list(pending_parallel),
                )
            )
        return WorkflowToolSchedule(
            scheduler_mode=SCHEDULER_MODE,
            selected_tasks=list(selection.selected_tasks),
            parallel_read_group=list(selection.parallel_read_group),
            serialized_write_group=list(selection.serialized_write_group),
            phases=phases,
        )


def build_scheduler_summary(schedule: WorkflowToolSchedule) -> dict[str, object]:
    return {
        "mode": schedule.scheduler_mode,
        "selected_task_ids": [task.task_id for task in schedule.selected_tasks],
        "parallel_read_group": [task.task_id for task in schedule.parallel_read_group],
        "serialized_write_group": [task.task_id for task in schedule.serialized_write_group],
        "parallel_read_count": len(schedule.parallel_read_group),
        "serialized_write_count": len(schedule.serialized_write_group),
        "phase_count": len(schedule.phases),
    }
