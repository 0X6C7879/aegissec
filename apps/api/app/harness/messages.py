from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.db.models import AttachmentMetadata, MessageRole, SkillAgentSummaryRead


class ChatRuntimeError(RuntimeError):
    pass


class ChatRuntimeConfigurationError(ChatRuntimeError):
    pass


class ToolCallRequest(BaseModel):
    tool_name: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    mcp_server_id: str | None = None
    mcp_tool_name: str | None = None


class ToolCallResult(BaseModel):
    tool_name: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


@dataclass(slots=True)
class MCPToolBinding:
    server_id: str
    tool_name: str
    tool_alias: str
    input_schema: dict[str, Any]
    tool_description: str | None = None
    tool_title: str | None = None


@dataclass(slots=True)
class ConversationMessage:
    role: MessageRole
    content: str
    attachments: list[AttachmentMetadata] = field(default_factory=list)


TextDeltaHandler = Callable[[str], Awaitable[None]]
SummaryHandler = Callable[[str], Awaitable[None]]
CancelledChecker = Callable[[], bool]
ToolExecutor = Callable[[ToolCallRequest], Awaitable[ToolCallResult]]


@dataclass(slots=True)
class GenerationCallbacks:
    on_text_delta: TextDeltaHandler | None = None
    on_summary: SummaryHandler | None = None
    is_cancelled: CancelledChecker | None = None


class ChatRuntime(Protocol):
    async def generate_reply(
        self,
        content: str,
        attachments: Sequence[AttachmentMetadata],
        *,
        conversation_messages: Sequence[ConversationMessage] | None = None,
        available_skills: Sequence[SkillAgentSummaryRead] | None = None,
        mcp_tools: Sequence[Mapping[str, Any]] | None = None,
        skill_context_prompt: str | None = None,
        execute_tool: ToolExecutor | None = None,
        callbacks: GenerationCallbacks | None = None,
    ) -> str: ...


def _fallback_mcp_input_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }


def _normalize_mcp_input_schema(raw_schema: Any) -> dict[str, Any]:
    if not isinstance(raw_schema, Mapping):
        return _fallback_mcp_input_schema()

    schema = dict(raw_schema)
    schema_type = schema.get("type")
    properties = schema.get("properties")

    if schema_type != "object" or not isinstance(properties, Mapping):
        return _fallback_mcp_input_schema()

    normalized = dict(schema)
    normalized.setdefault("required", [])
    normalized.setdefault("additionalProperties", True)
    return normalized


def _normalize_mcp_tool_bindings(
    mcp_tools: Sequence[Mapping[str, Any]] | None,
) -> list[MCPToolBinding]:
    bindings: list[MCPToolBinding] = []
    for raw_tool in mcp_tools or []:
        if not isinstance(raw_tool, Mapping):
            continue
        tool_alias = raw_tool.get("tool_alias")
        server_id = raw_tool.get("server_id")
        tool_name = raw_tool.get("tool_name")
        if not all(
            isinstance(value, str) and value for value in (tool_alias, server_id, tool_name)
        ):
            continue
        normalized_tool_alias = str(tool_alias)
        normalized_server_id = str(server_id)
        normalized_tool_name = str(tool_name)
        bindings.append(
            MCPToolBinding(
                server_id=normalized_server_id,
                tool_name=normalized_tool_name,
                tool_alias=normalized_tool_alias,
                input_schema=_normalize_mcp_input_schema(raw_tool.get("input_schema")),
                tool_description=(
                    raw_tool.get("tool_description")
                    if isinstance(raw_tool.get("tool_description"), str)
                    else None
                ),
                tool_title=(
                    raw_tool.get("tool_title")
                    if isinstance(raw_tool.get("tool_title"), str)
                    else None
                ),
            )
        )
    return bindings


def _find_mcp_tool_binding(
    tool_name: str,
    mcp_tools: Sequence[Mapping[str, Any]] | None,
) -> MCPToolBinding | None:
    normalized_name = tool_name.strip()
    if not normalized_name:
        return None
    for binding in _normalize_mcp_tool_bindings(mcp_tools):
        if binding.tool_alias == normalized_name:
            return binding
    return None


@dataclass(slots=True)
class QueryUsage:
    model_turns: int = 0
    tool_rounds: int = 0
    tool_calls: int = 0


@dataclass(slots=True)
class ProviderTurnResult:
    assistant_payload: dict[str, Any]
    text_content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
