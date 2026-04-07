from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel


class ToolRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    DESTRUCTIVE = "destructive"


class MutatingTargetClass(StrEnum):
    NONE = "none"
    RUNTIME = "runtime"
    GRAPH = "graph"
    MEMORY = "memory"
    CONFIG = "config"
    SESSION = "session"


@dataclass(slots=True)
class ToolExecutionContext:
    session: Any
    assistant_message: Any
    runtime_service: Any
    skill_service: Any
    mcp_service: Any
    available_skills: list[Any]
    session_state: Any | None = None
    swarm_coordinator: Any | None = None


@dataclass(slots=True)
class ToolResult:
    tool_name: str
    payload: dict[str, Any]
    status: str = "completed"
    safe_summary: str | None = None
    transcript_tool_call_metadata: dict[str, Any] = field(default_factory=dict)
    transcript_result_metadata: dict[str, Any] = field(default_factory=dict)
    event_payload: dict[str, Any] = field(default_factory=dict)
    trace_entry: dict[str, Any] = field(default_factory=dict)
    step_metadata: dict[str, Any] = field(default_factory=dict)
    semantic_deltas: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class ToolHookContext:
    tool_request: Any
    tool: BaseTool[Any]
    execution_context: ToolExecutionContext
    decision: Any
    result: ToolResult | None = None
    error: Exception | None = None


class ToolExecutionHooks(Protocol):
    async def before_execution(self, context: ToolHookContext) -> None: ...

    async def after_execution(self, context: ToolHookContext) -> None: ...

    async def on_execution_error(self, context: ToolHookContext) -> None: ...


class NoOpToolExecutionHooks:
    async def before_execution(self, context: ToolHookContext) -> None:
        del context

    async def after_execution(self, context: ToolHookContext) -> None:
        del context

    async def on_execution_error(self, context: ToolHookContext) -> None:
        del context


class BaseTool[InputModelT: BaseModel](ABC):
    name: str
    description: str
    input_model: type[InputModelT]
    scope_sensitive: bool = False
    evidence_effects: bool = False

    def parse_arguments(self, arguments: Mapping[str, Any]) -> InputModelT:
        return self.input_model.model_validate(dict(arguments))

    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    def is_read_only(self) -> bool:
        return False

    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.LOW

    def capability_tags(self) -> tuple[str, ...]:
        return ()

    def mutating_target_class(self) -> MutatingTargetClass:
        return MutatingTargetClass.NONE

    def to_openai_tool_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema(),
            },
        }

    def to_anthropic_tool_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema(),
        }

    @abstractmethod
    async def execute(
        self,
        context: ToolExecutionContext,
        arguments: Mapping[str, Any],
    ) -> ToolResult:
        raise NotImplementedError
