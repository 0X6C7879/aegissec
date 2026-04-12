from __future__ import annotations

import importlib
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.compat.mcp.service import MCPDisabledServerError, MCPInvalidToolError
from app.compat.skills.service import SkillContentReadError, SkillLookupError
from app.db.models import (
    RuntimeExecuteRequest,
    RuntimePolicy,
    TerminalExecuteRequest,
    TerminalSessionCreateRequest,
)
from app.services.runtime import (
    RuntimeArtifactPathError,
    RuntimeOperationError,
    RuntimePolicyViolationError,
)

from ..messages import ChatRuntimeError, MCPToolBinding, _normalize_mcp_tool_bindings
from .base import (
    BaseTool,
    MutatingTargetClass,
    ToolExecutionContext,
    ToolResult,
    ToolRiskLevel,
)
from .registry import ToolHookRegistry, ToolRegistry


class _NoArgumentsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecuteKaliCommandInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1)
    timeout_seconds: int | None = Field(default=None, gt=0)
    artifact_paths: list[str] = Field(default_factory=list)

    @field_validator("command")
    @classmethod
    def _validate_command(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Tool execute_kali_command requires a non-empty command.")
        return normalized

    @field_validator("artifact_paths")
    @classmethod
    def _validate_artifact_paths(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    "Tool execute_kali_command requires artifact_paths to be a list of strings."
                )
            normalized.append(item)
        return normalized


class ExecuteSkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_name_or_id: str = Field(min_length=1)
    current_prompt: str | None = None
    user_goal: str | None = None
    use_selected_skill_set: bool = False

    @field_validator("skill_name_or_id")
    @classmethod
    def _validate_skill_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Tool execute_skill requires a non-empty skill_name_or_id.")
        return normalized


class ReadSkillContentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_name_or_id: str = Field(min_length=1)

    @field_validator("skill_name_or_id")
    @classmethod
    def _validate_skill_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Tool read_skill_content requires a non-empty skill_name_or_id.")
        return normalized


class MCPPassthroughInput(BaseModel):
    model_config = ConfigDict(extra="allow")


class SpawnSubagentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_name: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SendSubagentMessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StopSubagentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(min_length=1)
    reason: str | None = None
    force: bool = False


class CreateTerminalSessionInput(TerminalSessionCreateRequest):
    pass


class ExecuteTerminalCommandInput(TerminalExecuteRequest):
    terminal_id: str = Field(min_length=1)

    @field_validator("terminal_id")
    @classmethod
    def _validate_terminal_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Tool execute_terminal_command requires a non-empty terminal_id.")
        return normalized


class ReadTerminalBufferInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terminal_id: str = Field(min_length=1)
    job_id: str = Field(min_length=1)
    stream: str = "stdout"
    lines: int = Field(default=200, ge=1, le=2_000)

    @field_validator("terminal_id", "job_id")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Terminal identifiers must not be blank.")
        return normalized

    @field_validator("stream")
    @classmethod
    def _validate_stream(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"stdout", "stderr"}:
            raise ValueError("Tool read_terminal_buffer requires stream to be stdout or stderr.")
        return normalized


class StopTerminalJobInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1)

    @field_validator("job_id")
    @classmethod
    def _validate_job_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Tool stop_terminal_job requires a non-empty job_id.")
        return normalized


class ExecuteKaliCommandTool(BaseTool[ExecuteKaliCommandInput]):
    name = "execute_kali_command"
    description = "Execute an allowed command in the Kali runtime."
    input_model = ExecuteKaliCommandInput
    scope_sensitive = True
    evidence_effects = True

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute inside the Kali runtime.",
                },
                "timeout_seconds": {
                    "type": ["integer", "null"],
                    "description": "Optional timeout in seconds.",
                },
                "artifact_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional artifact paths to collect from the runtime.",
                },
            },
            "required": ["command"],
            "additionalProperties": False,
        }

    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.HIGH

    def capability_tags(self) -> tuple[str, ...]:
        return ("runtime", "kali", "command")

    def mutating_target_class(self) -> MutatingTargetClass:
        return MutatingTargetClass.RUNTIME

    async def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        parsed = self.parse_arguments(arguments)
        try:
            runtime_policy = RuntimePolicy.model_validate(context.session.runtime_policy_json or {})
        except ValidationError as exc:
            message = exc.errors()[0].get("msg", "Unknown runtime policy validation error.")
            raise ChatRuntimeError(f"Invalid runtime policy: {message}") from exc
        try:
            run = context.runtime_service.execute(
                RuntimeExecuteRequest(
                    session_id=context.session.id,
                    command=parsed.command,
                    timeout_seconds=parsed.timeout_seconds,
                    artifact_paths=parsed.artifact_paths,
                ),
                runtime_policy=runtime_policy,
            )
        except (
            RuntimeArtifactPathError,
            RuntimeOperationError,
            RuntimePolicyViolationError,
        ) as exc:
            raise ChatRuntimeError(str(exc)) from exc

        payload = {
            "command": run.command,
            "status": run.status.value,
            "exit_code": run.exit_code,
            "stdout": run.stdout,
            "stderr": run.stderr,
            "artifacts": [artifact.relative_path for artifact in run.artifacts],
        }
        return ToolResult(
            tool_name=self.name,
            payload=payload,
            status=run.status.value,
            safe_summary=f"命令已完成，状态：{run.status.value}。",
            transcript_tool_call_metadata={"status": run.status.value, "run_id": run.id},
            transcript_result_metadata={
                "arguments": dict(arguments),
                "result": payload,
                "run_id": run.id,
                "command": run.command,
                "status": run.status.value,
                "exit_code": run.exit_code,
                "stdout": run.stdout,
                "stderr": run.stderr,
                "artifacts": payload["artifacts"],
            },
            event_payload={
                "run_id": run.id,
                "command": run.command,
                "status": run.status.value,
                "exit_code": run.exit_code,
                "requested_timeout_seconds": run.requested_timeout_seconds,
                "stdout": run.stdout,
                "stderr": run.stderr,
                "created_at": run.created_at.isoformat(),
                "artifact_paths": payload["artifacts"],
            },
            trace_entry={"status": run.status.value},
            step_metadata={"result": payload, "run_id": run.id, "status": run.status.value},
            semantic_deltas=[
                {
                    "semantic_id": f"runtime:{run.id}",
                    "source": self.name,
                    "evidence_ids": [f"runtime:{run.id}"],
                    "graph_hints": [
                        {
                            "graph_type": "attack",
                            "op": "hint",
                            "stable_key": f"runtime:{run.id}",
                            "run_id": run.id,
                            "command": run.command,
                            "status": run.status.value,
                        }
                    ],
                    "artifacts": payload["artifacts"],
                    "recent_tools": [self.name],
                    "reason": f"Runtime command completed with status {run.status.value}.",
                    "metadata": {
                        "run_id": run.id,
                        "command": run.command,
                        "artifact_paths": payload["artifacts"],
                    },
                }
            ],
        )


class CreateTerminalSessionTool(BaseTool[CreateTerminalSessionInput]):
    name = "create_terminal_session"
    description = "Create a new terminal session inside the current chat session."
    input_model = CreateTerminalSessionInput

    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.MEDIUM

    def capability_tags(self) -> tuple[str, ...]:
        return ("terminal", "session")

    def mutating_target_class(self) -> MutatingTargetClass:
        return MutatingTargetClass.SESSION

    async def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        terminal_session_service = context.terminal_session_service
        if terminal_session_service is None:
            raise ChatRuntimeError("Terminal session service is unavailable for this session.")
        parsed = self.parse_arguments(arguments)
        result = terminal_session_service.create_terminal(session=context.session, payload=parsed)
        payload = {"terminal": result.terminal.model_dump(mode="json", by_alias=True)}
        return ToolResult(
            tool_name=self.name,
            payload=payload,
            safe_summary=f"已创建终端 {result.terminal.title}。",
            transcript_result_metadata={"result": payload},
            event_payload=payload,
            trace_entry={"terminal_id": result.terminal.id},
            step_metadata={"result": payload},
        )


class ListTerminalSessionsTool(BaseTool[_NoArgumentsInput]):
    name = "list_terminal_sessions"
    description = "List terminal sessions available in the current chat session."
    input_model = _NoArgumentsInput

    def is_read_only(self) -> bool:
        return True

    def capability_tags(self) -> tuple[str, ...]:
        return ("terminal", "inventory")

    async def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        terminal_session_service = context.terminal_session_service
        if terminal_session_service is None:
            raise ChatRuntimeError("Terminal session service is unavailable for this session.")
        self.parse_arguments(arguments)
        terminals = terminal_session_service.list_terminals(session_id=context.session.id)
        payload = {
            "terminals": [terminal.model_dump(mode="json", by_alias=True) for terminal in terminals]
        }
        return ToolResult(
            tool_name=self.name,
            payload=payload,
            safe_summary="已列出当前会话的终端。",
            transcript_result_metadata={"result": payload},
            event_payload=payload,
            step_metadata={"result": payload},
        )


class ExecuteTerminalCommandTool(BaseTool[ExecuteTerminalCommandInput]):
    name = "execute_terminal_command"
    description = "Run a command in a terminal session, attached or as a detached background job."
    input_model = ExecuteTerminalCommandInput
    scope_sensitive = True
    evidence_effects = True

    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.HIGH

    def capability_tags(self) -> tuple[str, ...]:
        return ("terminal", "command")

    def mutating_target_class(self) -> MutatingTargetClass:
        return MutatingTargetClass.RUNTIME

    async def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        terminal_session_service = context.terminal_session_service
        terminal_runtime_service = context.terminal_runtime_service
        if terminal_session_service is None or terminal_runtime_service is None:
            raise ChatRuntimeError("Terminal runtime services are unavailable for this session.")
        parsed = self.parse_arguments(arguments)
        terminal = terminal_session_service.get_terminal(
            session_id=context.session.id,
            terminal_id=parsed.terminal_id,
        )
        if terminal is None:
            raise ChatRuntimeError("Terminal not found.")
        terminal_runtime = importlib.import_module("app.services.terminal_runtime")
        try:
            job_id, status = await terminal_runtime_service.execute_in_terminal(
                session_id=context.session.id,
                terminal_id=parsed.terminal_id,
                command=parsed.command,
                detach=parsed.detach,
                timeout_seconds=(
                    parsed.timeout_seconds
                    or context.runtime_service.resolve_policy_for_session(
                        context.session
                    ).max_execution_seconds
                ),
                artifact_paths=list(parsed.artifact_paths),
                runtime_policy=context.runtime_service.resolve_policy_for_session(context.session),
            )
        except (
            RuntimePolicyViolationError,
            terminal_runtime.TerminalRuntimeError,
        ) as exc:
            raise ChatRuntimeError(str(exc)) from exc
        payload = {
            "terminal_id": parsed.terminal_id,
            "detach": parsed.detach,
            "job_id": job_id,
            "status": status,
        }
        return ToolResult(
            tool_name=self.name,
            payload=payload,
            status=status,
            safe_summary=(
                f"已在终端 {terminal.title} 中启动后台命令。"
                if parsed.detach
                else f"已在终端 {terminal.title} 中发送命令。"
            ),
            transcript_result_metadata={"arguments": dict(arguments), "result": payload},
            event_payload=payload,
            trace_entry={"terminal_id": parsed.terminal_id, "job_id": job_id, "status": status},
            step_metadata={"result": payload},
        )


class ReadTerminalBufferTool(BaseTool[ReadTerminalBufferInput]):
    name = "read_terminal_buffer"
    description = "Read the latest buffered output for a detached terminal job."
    input_model = ReadTerminalBufferInput

    def is_read_only(self) -> bool:
        return True

    def capability_tags(self) -> tuple[str, ...]:
        return ("terminal", "buffer")

    async def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        terminal_session_service = context.terminal_session_service
        terminal_runtime_service = context.terminal_runtime_service
        if terminal_session_service is None or terminal_runtime_service is None:
            raise ChatRuntimeError("Terminal runtime services are unavailable for this session.")
        parsed = self.parse_arguments(arguments)
        terminal = terminal_session_service.get_terminal(
            session_id=context.session.id,
            terminal_id=parsed.terminal_id,
        )
        if terminal is None:
            raise ChatRuntimeError("Terminal not found.")
        job = terminal_session_service.get_terminal_job(
            session_id=context.session.id,
            job_id=parsed.job_id,
        )
        if job is None or job.terminal_session_id != parsed.terminal_id:
            raise ChatRuntimeError("Terminal job not found.")
        live_tail = await terminal_runtime_service.get_background_job_tail(
            session_id=context.session.id,
            job_id=parsed.job_id,
            stream=parsed.stream,
            lines=parsed.lines,
        )
        if live_tail is None:
            live_tail = terminal_session_service.get_persisted_terminal_job_tail(
                session_id=context.session.id,
                job_id=parsed.job_id,
                stream=parsed.stream,
                lines=parsed.lines,
            )
        tail = terminal_session_service.build_terminal_job_tail(
            job=job,
            stream=parsed.stream,
            lines=parsed.lines,
            content=live_tail,
        )
        payload = tail.model_dump(mode="json", by_alias=True)
        return ToolResult(
            tool_name=self.name,
            payload=payload,
            safe_summary=f"已读取终端任务 {parsed.job_id} 的输出缓冲。",
            transcript_result_metadata={"result": payload},
            event_payload=payload,
            trace_entry={"terminal_id": parsed.terminal_id, "job_id": parsed.job_id},
            step_metadata={"result": payload},
        )


class StopTerminalJobTool(BaseTool[StopTerminalJobInput]):
    name = "stop_terminal_job"
    description = "Stop a detached terminal job that is still running."
    input_model = StopTerminalJobInput

    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.DESTRUCTIVE

    def capability_tags(self) -> tuple[str, ...]:
        return ("terminal", "stop")

    def mutating_target_class(self) -> MutatingTargetClass:
        return MutatingTargetClass.RUNTIME

    async def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        terminal_session_service = context.terminal_session_service
        terminal_runtime_service = context.terminal_runtime_service
        if terminal_session_service is None or terminal_runtime_service is None:
            raise ChatRuntimeError("Terminal runtime services are unavailable for this session.")
        parsed = self.parse_arguments(arguments)
        existing_job = terminal_session_service.get_terminal_job(
            session_id=context.session.id,
            job_id=parsed.job_id,
        )
        if existing_job is None:
            raise ChatRuntimeError("Terminal job not found.")
        stopped = await terminal_runtime_service.stop_background_job(
            session_id=context.session.id,
            job_id=parsed.job_id,
        )
        if not stopped:
            raise ChatRuntimeError("Terminal job is not currently live.")
        refreshed_job = terminal_session_service.get_terminal_job(
            session_id=context.session.id,
            job_id=parsed.job_id,
        )
        if refreshed_job is None:
            raise ChatRuntimeError("Terminal job not found.")
        payload = {"job": refreshed_job.model_dump(mode="json", by_alias=True)}
        return ToolResult(
            tool_name=self.name,
            payload=payload,
            safe_summary=f"已停止终端任务 {parsed.job_id}。",
            transcript_result_metadata={"result": payload},
            event_payload=payload,
            trace_entry={"job_id": parsed.job_id},
            step_metadata={"result": payload},
        )


class ListAvailableSkillsTool(BaseTool[_NoArgumentsInput]):
    name = "list_available_skills"
    description = "List the skills currently available to this session."
    input_model = _NoArgumentsInput

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }

    def is_read_only(self) -> bool:
        return True

    def capability_tags(self) -> tuple[str, ...]:
        return ("skills", "inventory")

    async def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        self.parse_arguments(arguments)
        payload = {
            "skills": [skill.model_dump(mode="json") for skill in context.available_skills],
        }
        return ToolResult(
            tool_name=self.name,
            payload=payload,
            safe_summary="已列出当前可用技能。",
            transcript_result_metadata={"result": payload},
            event_payload={"result": payload},
            step_metadata={"result": payload},
        )


class ExecuteSkillTool(BaseTool[ExecuteSkillInput]):
    name = "execute_skill"
    description = "Prepare a skill or selected skill set for the current session."
    input_model = ExecuteSkillInput

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name_or_id": {
                    "type": "string",
                    "description": "Skill identifier, directory name, or display name.",
                }
            },
            "required": ["skill_name_or_id"],
            "additionalProperties": False,
        }

    def capability_tags(self) -> tuple[str, ...]:
        return ("skills", "context")

    async def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        parsed = self.parse_arguments(arguments)
        try:
            if parsed.use_selected_skill_set and parsed.current_prompt:
                result_payload = context.skill_service.prepare_best_skill(
                    session_id=context.session.id,
                    current_prompt=parsed.current_prompt,
                    user_goal=parsed.user_goal or parsed.current_prompt,
                    include_reference_only=True,
                    preferred_skill_identifier=parsed.skill_name_or_id,
                )
            else:
                result_payload = context.skill_service.execute_skill_by_name_or_directory_name(
                    parsed.skill_name_or_id,
                    session_id=context.session.id,
                    current_prompt=parsed.current_prompt,
                    user_goal=parsed.user_goal,
                )
        except (SkillLookupError, SkillContentReadError) as exc:
            raise ChatRuntimeError(str(exc)) from exc

        skill_payload = dict(result_payload)
        skill_dict = skill_payload.get("skill")
        skill_label = parsed.skill_name_or_id
        if isinstance(skill_dict, Mapping) and isinstance(skill_dict.get("directory_name"), str):
            skill_label = skill_dict["directory_name"]
        transcript_result_metadata = {"result": skill_payload}
        if isinstance(skill_payload.get("execution"), Mapping):
            transcript_result_metadata["execution"] = dict(skill_payload["execution"])
        if isinstance(skill_dict, Mapping):
            transcript_result_metadata["skill"] = dict(skill_dict)

        return ToolResult(
            tool_name=self.name,
            payload=skill_payload,
            safe_summary=f"已准备 {skill_label} 技能上下文。",
            transcript_result_metadata=transcript_result_metadata,
            event_payload={"result": skill_payload},
            step_metadata={"result": skill_payload},
        )


class ReadSkillContentTool(BaseTool[ReadSkillContentInput]):
    name = "read_skill_content"
    description = "Read the resolved content of a skill."
    input_model = ReadSkillContentInput

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name_or_id": {
                    "type": "string",
                    "description": "Skill identifier, directory name, or display name.",
                }
            },
            "required": ["skill_name_or_id"],
            "additionalProperties": False,
        }

    def is_read_only(self) -> bool:
        return True

    def capability_tags(self) -> tuple[str, ...]:
        return ("skills", "content")

    async def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        parsed = self.parse_arguments(arguments)
        try:
            skill_content = context.skill_service.read_skill_content_by_name_or_directory_name(
                parsed.skill_name_or_id
            )
        except (SkillLookupError, SkillContentReadError) as exc:
            raise ChatRuntimeError(str(exc)) from exc

        payload = {"skill": skill_content.model_dump(mode="json")}
        return ToolResult(
            tool_name=self.name,
            payload=payload,
            safe_summary=f"已读取 {parsed.skill_name_or_id} 的技能内容。",
            transcript_result_metadata={"result": payload},
            event_payload={"result": payload},
            step_metadata={"result": payload},
        )


class MCPPassthroughTool(BaseTool[MCPPassthroughInput]):
    input_model = MCPPassthroughInput
    scope_sensitive = True
    evidence_effects = True

    def __init__(self, binding: MCPToolBinding) -> None:
        self._binding = binding
        self.name = binding.tool_alias
        self.description = (
            binding.tool_description
            or binding.tool_title
            or f"Call MCP tool {binding.server_id}/{binding.tool_name}."
        )

    def input_schema(self) -> dict[str, Any]:
        return self._binding.input_schema

    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.MEDIUM

    def capability_tags(self) -> tuple[str, ...]:
        return ("mcp", self._binding.server_id)

    def mutating_target_class(self) -> MutatingTargetClass:
        return MutatingTargetClass.SESSION

    async def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        payload_arguments = self.parse_arguments(arguments).model_dump(mode="python")
        try:
            result = await context.mcp_service.call_tool(
                self._binding.server_id,
                self._binding.tool_name,
                payload_arguments,
            )
        except (MCPDisabledServerError, MCPInvalidToolError) as exc:
            raise ChatRuntimeError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ChatRuntimeError(str(exc)) from exc
        payload = {
            "server_id": self._binding.server_id,
            "tool_name": self._binding.tool_name,
            "result": result or {},
        }
        return ToolResult(
            tool_name=self.name,
            payload=payload,
            safe_summary=(f"已调用 MCP 工具 {self.name} -> {self._binding.tool_name}。"),
            transcript_tool_call_metadata={
                "mcp_server_id": self._binding.server_id,
                "mcp_tool_name": self._binding.tool_name,
            },
            transcript_result_metadata={
                "arguments": dict(arguments),
                "result": payload,
                "mcp_server_id": self._binding.server_id,
                "mcp_tool_name": self._binding.tool_name,
            },
            event_payload={
                "mcp_server_id": self._binding.server_id,
                "mcp_tool_name": self._binding.tool_name,
                "result": payload,
            },
            trace_entry={
                "mcp_server_id": self._binding.server_id,
                "mcp_tool_name": self._binding.tool_name,
            },
            step_metadata={
                "result": payload,
                "mcp_server_id": self._binding.server_id,
                "mcp_tool_name": self._binding.tool_name,
            },
            semantic_deltas=[
                {
                    "semantic_id": f"mcp:{self._binding.server_id}:{self._binding.tool_name}",
                    "source": self.name,
                    "graph_hints": [
                        {
                            "graph_type": "attack",
                            "op": "hint",
                            "stable_key": (
                                f"mcp:{self._binding.server_id}:{self._binding.tool_name}"
                            ),
                            "mcp_server_id": self._binding.server_id,
                            "mcp_tool_name": self._binding.tool_name,
                        }
                    ],
                    "recent_tools": [self.name],
                    "reason": f"MCP tool {self._binding.tool_name} executed.",
                    "metadata": {
                        "mcp_server_id": self._binding.server_id,
                        "mcp_tool_name": self._binding.tool_name,
                    },
                }
            ],
        )


class SpawnSubagentTool(BaseTool[SpawnSubagentInput]):
    name = "spawn_subagent"
    description = "Spawn an in-process swarm subagent with a bounded objective."
    input_model = SpawnSubagentInput

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "profile_name": {"type": "string"},
                "objective": {"type": "string"},
                "metadata": {"type": "object"},
            },
            "required": ["profile_name", "objective"],
            "additionalProperties": False,
        }

    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.MEDIUM

    def capability_tags(self) -> tuple[str, ...]:
        return ("swarm", "spawn")

    def mutating_target_class(self) -> MutatingTargetClass:
        return MutatingTargetClass.SESSION

    async def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        swarm_coordinator = getattr(context, "swarm_coordinator", None)
        if swarm_coordinator is None:
            raise ChatRuntimeError("Swarm coordinator is unavailable for this session.")
        parsed = self.parse_arguments(arguments)
        payload = await swarm_coordinator.spawn_agent(
            profile_name=parsed.profile_name,
            objective=parsed.objective,
            metadata=parsed.metadata,
        )
        return ToolResult(
            tool_name=self.name,
            payload=payload,
            safe_summary=f"已创建 {parsed.profile_name} 子代理。",
            transcript_result_metadata={"result": payload},
            event_payload={
                "result": payload,
                "swarm_notifications": payload.get("notifications", []),
            },
            trace_entry={
                "swarm": {"action": "spawn", "agent_id": payload.get("agent", {}).get("agent_id")}
            },
            step_metadata={"result": payload},
        )


class SendSubagentMessageTool(BaseTool[SendSubagentMessageInput]):
    name = "send_subagent_message"
    description = "Send a structured message to an existing in-process swarm subagent."
    input_model = SendSubagentMessageInput

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "content": {"type": "string"},
                "metadata": {"type": "object"},
            },
            "required": ["agent_id", "content"],
            "additionalProperties": False,
        }

    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.LOW

    def capability_tags(self) -> tuple[str, ...]:
        return ("swarm", "mailbox")

    def mutating_target_class(self) -> MutatingTargetClass:
        return MutatingTargetClass.SESSION

    async def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        swarm_coordinator = getattr(context, "swarm_coordinator", None)
        if swarm_coordinator is None:
            raise ChatRuntimeError("Swarm coordinator is unavailable for this session.")
        parsed = self.parse_arguments(arguments)
        payload = await swarm_coordinator.send_message(
            agent_id=parsed.agent_id,
            content=parsed.content,
            metadata=parsed.metadata,
        )
        return ToolResult(
            tool_name=self.name,
            payload=payload,
            safe_summary=f"已向子代理 {parsed.agent_id} 发送消息。",
            transcript_result_metadata={"result": payload},
            event_payload={
                "result": payload,
                "swarm_notifications": payload.get("notifications", []),
            },
            trace_entry={"swarm": {"action": "message", "agent_id": parsed.agent_id}},
            step_metadata={"result": payload},
        )


class StopSubagentTool(BaseTool[StopSubagentInput]):
    name = "stop_subagent"
    description = "Stop an in-process swarm subagent."
    input_model = StopSubagentInput

    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "reason": {"type": ["string", "null"]},
                "force": {"type": "boolean"},
            },
            "required": ["agent_id"],
            "additionalProperties": False,
        }

    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.DESTRUCTIVE

    def capability_tags(self) -> tuple[str, ...]:
        return ("swarm", "stop")

    def mutating_target_class(self) -> MutatingTargetClass:
        return MutatingTargetClass.SESSION

    async def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        swarm_coordinator = getattr(context, "swarm_coordinator", None)
        if swarm_coordinator is None:
            raise ChatRuntimeError("Swarm coordinator is unavailable for this session.")
        parsed = self.parse_arguments(arguments)
        payload = await swarm_coordinator.stop_agent(
            agent_id=parsed.agent_id,
            reason=parsed.reason,
            force=parsed.force,
        )
        return ToolResult(
            tool_name=self.name,
            payload=payload,
            safe_summary=f"已停止子代理 {parsed.agent_id}。",
            transcript_result_metadata={"result": payload},
            event_payload={
                "result": payload,
                "swarm_notifications": payload.get("notifications", []),
            },
            trace_entry={"swarm": {"action": "stop", "agent_id": parsed.agent_id}},
            step_metadata={"result": payload},
        )


def build_default_tool_registry(
    *,
    mcp_tools: Sequence[Mapping[str, Any]] | None = None,
    include_swarm_tools: bool = False,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ExecuteKaliCommandTool())
    registry.register(ListAvailableSkillsTool())
    registry.register(ExecuteSkillTool())
    registry.register(ReadSkillContentTool())
    registry.register(CreateTerminalSessionTool())
    registry.register(ListTerminalSessionsTool())
    registry.register(ExecuteTerminalCommandTool())
    registry.register(ReadTerminalBufferTool())
    registry.register(StopTerminalJobTool())
    if include_swarm_tools:
        registry.register(SpawnSubagentTool())
        registry.register(SendSubagentMessageTool())
        registry.register(StopSubagentTool())
    for binding in _normalize_mcp_tool_bindings(mcp_tools):
        registry.register(MCPPassthroughTool(binding))
    return registry


def build_default_tool_hook_registry() -> ToolHookRegistry:
    harness_hooks = importlib.import_module("app.harness.hooks")
    registry = ToolHookRegistry()
    registry.register_global(harness_hooks.PreToolUseHook())
    registry.register_global(harness_hooks.PostToolUseHook())
    registry.register_global(harness_hooks.PostEvidenceIngestHook())
    registry.register_global(harness_hooks.OnExecutionErrorHook())
    return registry
