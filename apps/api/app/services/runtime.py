from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from threading import Thread
from time import sleep
from typing import Any, Protocol, cast

import docker
from docker.errors import DockerException, ImageNotFound, NotFound
from fastapi import Depends
from sqlmodel import Session as DBSession

from app.core.settings import Settings, get_settings
from app.db.models import (
    ExecutionStatus,
    RuntimeContainerStateRead,
    RuntimeContainerStatus,
    RuntimeExecuteRequest,
    RuntimeExecutionRunRead,
    RuntimeStatusRead,
    to_runtime_artifact_read,
    to_runtime_execution_run_read,
    utc_now,
)
from app.db.repositories import RuntimeRepository
from app.db.session import get_db_session

SHELL_PATH = "/bin/zsh"


class RuntimeServiceError(Exception):
    pass


class RuntimeArtifactPathError(RuntimeServiceError):
    pass


class RuntimeOperationError(RuntimeServiceError):
    pass


@dataclass(slots=True)
class RuntimeContainerState:
    status: RuntimeContainerStatus
    container_name: str
    image: str
    workspace_host_path: str
    workspace_container_path: str
    container_id: str | None = None
    started_at: datetime | None = None


@dataclass(slots=True)
class RuntimeCommandResult:
    status: ExecutionStatus
    exit_code: int | None
    stdout: str
    stderr: str
    started_at: datetime
    ended_at: datetime
    container_state: RuntimeContainerState


class RuntimeBackend(Protocol):
    def inspect(self) -> RuntimeContainerState: ...

    def ensure_started(self) -> RuntimeContainerState: ...

    def stop(self) -> RuntimeContainerState: ...

    def execute(
        self,
        command: str,
        timeout_seconds: int,
        artifact_paths: list[str],
    ) -> RuntimeCommandResult: ...


class DockerExecApi(Protocol):
    def exec_start(self, exec_id: str, *, stream: bool, demux: bool) -> Iterable[object]: ...

    def exec_create(
        self,
        container_id: str,
        *,
        cmd: list[str],
        stdout: bool,
        stderr: bool,
        stdin: bool,
        tty: bool,
        workdir: str,
    ) -> dict[str, object]: ...

    def exec_inspect(self, exec_id: str) -> dict[str, object]: ...


class DockerContainer(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def attrs(self) -> dict[str, object]: ...

    def reload(self) -> None: ...

    def start(self) -> None: ...

    def stop(self, timeout: int = 0) -> None: ...

    def exec_run(
        self,
        cmd: list[str],
        *,
        stdout: bool,
        stderr: bool,
    ) -> object: ...


class DockerContainersClient(Protocol):
    def get(self, container_name: str) -> DockerContainer: ...

    def create(self, **kwargs: object) -> DockerContainer: ...


class DockerImagesClient(Protocol):
    def get(self, image_name: str) -> object: ...


class DockerClientProtocol(Protocol):
    @property
    def containers(self) -> DockerContainersClient: ...

    @property
    def images(self) -> DockerImagesClient: ...

    @property
    def api(self) -> DockerExecApi: ...


class _ExecCollector:
    def __init__(self, api_client: Any, exec_id: str) -> None:
        self._api_client = api_client
        self._exec_id = exec_id
        self.stdout_chunks: list[bytes] = []
        self.stderr_chunks: list[bytes] = []
        self.error: RuntimeOperationError | None = None

    def collect(self) -> None:
        try:
            stream = self._api_client.exec_start(self._exec_id, stream=True, demux=True)
            for chunk in stream:
                if not isinstance(chunk, tuple) or len(chunk) != 2:
                    continue

                stdout_chunk, stderr_chunk = chunk
                if isinstance(stdout_chunk, bytes) and stdout_chunk:
                    self.stdout_chunks.append(stdout_chunk)
                if isinstance(stderr_chunk, bytes) and stderr_chunk:
                    self.stderr_chunks.append(stderr_chunk)
        except DockerException:
            self.error = RuntimeOperationError("Failed to collect command output from Docker.")

    def stdout_text(self) -> str:
        return b"".join(self.stdout_chunks).decode("utf-8", errors="replace")

    def stderr_text(self) -> str:
        return b"".join(self.stderr_chunks).decode("utf-8", errors="replace")


class DockerRuntimeBackend:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._workspace_dir = Path(settings.runtime_workspace_dir).resolve()
        self._workspace_dir.mkdir(parents=True, exist_ok=True)
        try:
            client = docker.from_env()
            self._client = client
            self._api_client = client.api
        except DockerException as exc:
            raise RuntimeOperationError(
                "Docker is not available. Start Docker Desktop or the daemon."
            ) from exc

    def inspect(self) -> RuntimeContainerState:
        container = self._get_container()
        if container is None:
            return self._missing_state()

        container.reload()
        return self._to_container_state(container)

    def ensure_started(self) -> RuntimeContainerState:
        container = self._get_container()
        if container is None:
            container = self._create_container()

        container.reload()
        if getattr(container, "status", None) != "running":
            try:
                container.start()
                container.reload()
            except DockerException as exc:
                raise RuntimeOperationError("Failed to start the Kali runtime container.") from exc

        return self._to_container_state(container)

    def stop(self) -> RuntimeContainerState:
        container = self._get_container()
        if container is None:
            return self._missing_state()

        container.reload()
        if getattr(container, "status", None) == "running":
            try:
                container.stop(timeout=5)
                container.reload()
            except DockerException as exc:
                raise RuntimeOperationError("Failed to stop the Kali runtime container.") from exc

        return self._to_container_state(container)

    def execute(
        self,
        command: str,
        timeout_seconds: int,
        artifact_paths: list[str],
    ) -> RuntimeCommandResult:
        del artifact_paths

        container_state = self.ensure_started()
        container = self._require_container()
        started_at = utc_now()

        try:
            exec_payload = self._api_client.exec_create(
                container.id,
                cmd=[SHELL_PATH, "-lc", command],
                stdout=True,
                stderr=True,
                stdin=False,
                tty=False,
                workdir=self._settings.runtime_workspace_container_path,
            )
        except DockerException as exc:
            raise RuntimeOperationError(
                "Failed to create a Docker exec instance for the command."
            ) from exc

        exec_id = exec_payload.get("Id")
        if not isinstance(exec_id, str) or not exec_id:
            raise RuntimeOperationError("Docker did not return a valid exec identifier.")

        collector = _ExecCollector(self._api_client, exec_id)
        thread = Thread(target=collector.collect, daemon=True)
        thread.start()
        thread.join(timeout=float(timeout_seconds))

        timed_out = thread.is_alive()
        if timed_out:
            self._terminate_exec(container, exec_id)
            thread.join(timeout=2.0)

        if collector.error is not None and not timed_out:
            raise collector.error

        ended_at = utc_now()
        exec_details = self._inspect_exec(exec_id)
        exit_code = exec_details.get("ExitCode")
        if not isinstance(exit_code, int):
            exit_code = None

        stdout = collector.stdout_text()
        stderr = collector.stderr_text()
        if timed_out:
            timeout_message = f"Command timed out after {timeout_seconds} seconds."
            stderr = f"{stderr}\n{timeout_message}".strip()
            status = ExecutionStatus.TIMEOUT
            if exit_code is None:
                exit_code = 124
        else:
            status = ExecutionStatus.SUCCESS if exit_code == 0 else ExecutionStatus.FAILED

        return RuntimeCommandResult(
            status=status,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            started_at=started_at,
            ended_at=ended_at,
            container_state=container_state,
        )

    def _get_container(self) -> DockerContainer | None:
        try:
            return cast(
                DockerContainer,
                self._client.containers.get(self._settings.runtime_container_name),
            )
        except NotFound:
            return None
        except DockerException as exc:
            raise RuntimeOperationError("Failed to inspect the Kali runtime container.") from exc

    def _require_container(self) -> DockerContainer:
        container = self._get_container()
        if container is None:
            raise RuntimeOperationError("The Kali runtime container is not available.")
        return container

    def _create_container(self) -> DockerContainer:
        self._ensure_image_exists()
        try:
            return cast(
                DockerContainer,
                self._client.containers.create(
                    image=self._settings.kali_image,
                    name=self._settings.runtime_container_name,
                    command=[SHELL_PATH, "-lc", "while true; do sleep 3600; done"],
                    detach=True,
                    tty=False,
                    stdin_open=False,
                    working_dir=self._settings.runtime_workspace_container_path,
                    volumes={
                        str(self._workspace_dir): {
                            "bind": self._settings.runtime_workspace_container_path,
                            "mode": "rw",
                        }
                    },
                    labels={
                        "app": "aegissec",
                        "component": "runtime",
                    },
                ),
            )
        except DockerException as exc:
            raise RuntimeOperationError("Failed to create the Kali runtime container.") from exc

    def _ensure_image_exists(self) -> None:
        try:
            self._client.images.get(self._settings.kali_image)
        except ImageNotFound as exc:
            raise RuntimeOperationError(
                "Docker image "
                f"'{self._settings.kali_image}' is not available. Build it before "
                "starting the runtime."
            ) from exc
        except DockerException as exc:
            raise RuntimeOperationError(
                "Failed to inspect the configured Kali Docker image."
            ) from exc

    def _inspect_exec(self, exec_id: str) -> dict[str, object]:
        try:
            exec_details = self._api_client.exec_inspect(exec_id)
        except DockerException as exc:
            raise RuntimeOperationError("Failed to inspect the Docker exec state.") from exc

        if not isinstance(exec_details, dict):
            raise RuntimeOperationError("Docker returned an invalid exec inspection payload.")

        return exec_details

    def _terminate_exec(self, container: DockerContainer, exec_id: str) -> None:
        exec_details = self._inspect_exec(exec_id)
        pid = exec_details.get("Pid")
        if not isinstance(pid, int) or pid <= 0:
            return

        self._signal_exec(container, pid, "TERM")
        sleep(0.5)
        refreshed_exec_details = self._inspect_exec(exec_id)
        if bool(refreshed_exec_details.get("Running")):
            self._signal_exec(container, pid, "KILL")

    @staticmethod
    def _signal_exec(container: DockerContainer, pid: int, signal_name: str) -> None:
        try:
            container.exec_run(
                [SHELL_PATH, "-lc", f"kill -{signal_name} {pid}"],
                stdout=False,
                stderr=False,
            )
        except DockerException:
            return

    def _to_container_state(self, container: DockerContainer) -> RuntimeContainerState:
        raw_status = getattr(container, "status", None)
        status = (
            RuntimeContainerStatus.RUNNING
            if raw_status == "running"
            else RuntimeContainerStatus.STOPPED
        )
        container_id = getattr(container, "id", None)
        return RuntimeContainerState(
            status=status,
            container_name=self._settings.runtime_container_name,
            image=self._settings.kali_image,
            workspace_host_path=str(self._workspace_dir),
            workspace_container_path=self._settings.runtime_workspace_container_path,
            container_id=container_id if isinstance(container_id, str) else None,
            started_at=self._parse_started_at(container),
        )

    def _missing_state(self) -> RuntimeContainerState:
        return RuntimeContainerState(
            status=RuntimeContainerStatus.MISSING,
            container_name=self._settings.runtime_container_name,
            image=self._settings.kali_image,
            workspace_host_path=str(self._workspace_dir),
            workspace_container_path=self._settings.runtime_workspace_container_path,
        )

    @staticmethod
    def _parse_started_at(container: DockerContainer) -> datetime | None:
        attrs = getattr(container, "attrs", None)
        if not isinstance(attrs, dict):
            return None

        state = attrs.get("State")
        if not isinstance(state, dict):
            return None

        raw_started_at = state.get("StartedAt")
        if (
            not isinstance(raw_started_at, str)
            or not raw_started_at
            or raw_started_at.startswith("0001-")
        ):
            return None

        normalized = raw_started_at.replace("Z", "+00:00")
        try:
            started_at = datetime.fromisoformat(normalized)
        except ValueError:
            return None

        if started_at.tzinfo is None:
            return started_at.replace(tzinfo=UTC)
        return started_at


class RuntimeService:
    def __init__(
        self,
        settings: Settings,
        repository: RuntimeRepository,
        backend: RuntimeBackend,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._backend = backend
        self._workspace_dir = Path(settings.runtime_workspace_dir).resolve()
        self._workspace_dir.mkdir(parents=True, exist_ok=True)

    def get_status(self) -> RuntimeStatusRead:
        runtime_state = self._backend.inspect()
        recent_runs = self._repository.list_recent_runs(
            limit=self._settings.runtime_recent_runs_limit
        )
        recent_run_reads = [
            to_runtime_execution_run_read(run, self._repository.list_artifacts_for_run(run.id))
            for run in recent_runs
        ]
        recent_artifacts = [
            to_runtime_artifact_read(artifact)
            for artifact in self._repository.list_recent_artifacts(
                limit=self._settings.runtime_recent_artifacts_limit
            )
        ]
        return RuntimeStatusRead(
            runtime=self._to_container_state_read(runtime_state),
            recent_runs=recent_run_reads,
            recent_artifacts=recent_artifacts,
        )

    def start(self) -> RuntimeContainerStateRead:
        return self._to_container_state_read(self._backend.ensure_started())

    def stop(self) -> RuntimeContainerStateRead:
        return self._to_container_state_read(self._backend.stop())

    def execute(self, payload: RuntimeExecuteRequest) -> RuntimeExecutionRunRead:
        timeout_seconds = payload.timeout_seconds or self._settings.runtime_default_timeout_seconds
        command = payload.command.strip()
        if not command:
            raise RuntimeArtifactPathError("Command must not be empty.")

        result = self._backend.execute(command, timeout_seconds, payload.artifact_paths)
        artifacts = self._resolve_artifacts(payload.artifact_paths)
        run, artifact_rows = self._repository.create_run(
            session_id=payload.session_id,
            command=command,
            requested_timeout_seconds=timeout_seconds,
            status=result.status,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            container_name=result.container_state.container_name,
            started_at=result.started_at,
            ended_at=result.ended_at,
            artifacts=artifacts,
        )
        return to_runtime_execution_run_read(run, artifact_rows)

    def _resolve_artifacts(self, artifact_paths: list[str]) -> list[tuple[str, str, str]]:
        workspace_container_path = PurePosixPath(self._settings.runtime_workspace_container_path)
        normalized_artifacts: list[tuple[str, str, str]] = []
        seen_relative_paths: set[str] = set()

        for artifact_path in artifact_paths:
            relative_path = self._normalize_relative_artifact_path(artifact_path)
            if relative_path in seen_relative_paths:
                continue

            seen_relative_paths.add(relative_path)
            host_path = (self._workspace_dir / Path(relative_path)).resolve()
            if not host_path.is_relative_to(self._workspace_dir):
                raise RuntimeArtifactPathError(
                    "Artifact paths must stay within the runtime workspace."
                )
            if not host_path.exists():
                raise RuntimeArtifactPathError(
                    f"Artifact path '{artifact_path}' does not exist under the runtime workspace."
                )

            container_path = str(workspace_container_path / PurePosixPath(relative_path))
            normalized_artifacts.append((relative_path, str(host_path), container_path))

        return normalized_artifacts

    def _normalize_relative_artifact_path(self, artifact_path: str) -> str:
        cleaned_path = artifact_path.strip()
        if not cleaned_path:
            raise RuntimeArtifactPathError("Artifact paths must not be empty.")

        workspace_container_prefix = self._settings.runtime_workspace_container_path.rstrip("/")
        if cleaned_path == workspace_container_prefix or cleaned_path.startswith(
            f"{workspace_container_prefix}/"
        ):
            relative_container_path = PurePosixPath(cleaned_path).relative_to(
                PurePosixPath(self._settings.runtime_workspace_container_path)
            )
            return self._validate_relative_posix_path(relative_container_path)

        candidate_host_path = Path(cleaned_path)
        if candidate_host_path.is_absolute():
            resolved_host_path = candidate_host_path.resolve()
            if not resolved_host_path.is_relative_to(self._workspace_dir):
                raise RuntimeArtifactPathError(
                    "Artifact paths must stay within the runtime workspace."
                )
            relative_host_path = resolved_host_path.relative_to(self._workspace_dir).as_posix()
            return self._validate_relative_posix_path(PurePosixPath(relative_host_path))

        relative_path = PurePosixPath(cleaned_path.replace("\\", "/"))
        return self._validate_relative_posix_path(relative_path)

    @staticmethod
    def _validate_relative_posix_path(relative_path: PurePosixPath) -> str:
        if relative_path.is_absolute():
            raise RuntimeArtifactPathError(
                "Artifact paths must be relative to the runtime workspace."
            )
        if not relative_path.parts:
            raise RuntimeArtifactPathError("Artifact paths must not be empty.")
        if any(part in {"", ".", ".."} for part in relative_path.parts):
            raise RuntimeArtifactPathError("Artifact paths must not contain traversal segments.")
        return relative_path.as_posix()

    @staticmethod
    def _to_container_state_read(runtime_state: RuntimeContainerState) -> RuntimeContainerStateRead:
        return RuntimeContainerStateRead(
            status=runtime_state.status,
            container_name=runtime_state.container_name,
            image=runtime_state.image,
            container_id=runtime_state.container_id,
            workspace_host_path=runtime_state.workspace_host_path,
            workspace_container_path=runtime_state.workspace_container_path,
            started_at=runtime_state.started_at,
        )


def get_runtime_backend(settings: Settings = Depends(get_settings)) -> RuntimeBackend:
    return DockerRuntimeBackend(settings)


def get_runtime_service(
    db_session: DBSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
    backend: RuntimeBackend = Depends(get_runtime_backend),
) -> RuntimeService:
    return RuntimeService(settings, RuntimeRepository(db_session), backend)
