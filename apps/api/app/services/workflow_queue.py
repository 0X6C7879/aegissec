from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.core.settings import Settings
from app.db.models import RuntimeExecuteRequest, RuntimeExecutionRunRead, RuntimePolicy
from app.db.repositories import RunLogRepository, RuntimeRepository
from app.services.runtime import RuntimeBackend, RuntimeService, get_runtime_backend


class WorkflowQueueBackend(Protocol):
    def execute(
        self,
        payload: RuntimeExecuteRequest,
        runtime_policy: RuntimePolicy | None = None,
    ) -> RuntimeExecutionRunRead: ...


def execute_runtime_command(
    queue_backend: WorkflowQueueBackend,
    payload: RuntimeExecuteRequest,
    runtime_policy: RuntimePolicy | None = None,
) -> RuntimeExecutionRunRead:
    return queue_backend.execute(payload, runtime_policy=runtime_policy)


@dataclass(slots=True)
class InProcessWorkflowQueueBackend:
    settings: Settings
    runtime_repository: RuntimeRepository
    run_log_repository: RunLogRepository
    runtime_backend: RuntimeBackend

    def execute(
        self,
        payload: RuntimeExecuteRequest,
        runtime_policy: RuntimePolicy | None = None,
    ) -> RuntimeExecutionRunRead:
        runtime_service = RuntimeService(
            self.settings,
            self.runtime_repository,
            self.run_log_repository,
            self.runtime_backend,
        )
        return runtime_service.execute(payload, runtime_policy=runtime_policy)


@dataclass(slots=True)
class RedisWorkflowQueueBackend:
    settings: Settings
    runtime_repository: RuntimeRepository
    run_log_repository: RunLogRepository
    runtime_backend: RuntimeBackend

    def execute(
        self,
        payload: RuntimeExecuteRequest,
        runtime_policy: RuntimePolicy | None = None,
    ) -> RuntimeExecutionRunRead:
        runtime_service = RuntimeService(
            self.settings,
            self.runtime_repository,
            self.run_log_repository,
            self.runtime_backend,
        )
        return runtime_service.execute(payload, runtime_policy=runtime_policy)


def get_workflow_queue_backend(
    settings: Settings,
    runtime_repository: RuntimeRepository,
    run_log_repository: RunLogRepository,
) -> WorkflowQueueBackend:
    runtime_backend = get_runtime_backend(settings)
    if settings.queue_backend == "redis":
        return RedisWorkflowQueueBackend(
            settings=settings,
            runtime_repository=runtime_repository,
            run_log_repository=run_log_repository,
            runtime_backend=runtime_backend,
        )

    return InProcessWorkflowQueueBackend(
        settings=settings,
        runtime_repository=runtime_repository,
        run_log_repository=run_log_repository,
        runtime_backend=runtime_backend,
    )
