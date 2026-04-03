from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.agent.prompting import (
    SYSTEM_PROMPT,
    build_anthropic_prompt_assembly,
    build_openai_prompt_assembly,
    format_message_content,
    render_skill_catalog_context,
)
from app.core.settings import Settings, get_settings
from app.db.models import AttachmentMetadata, MessageRole, SkillAgentSummaryRead

MAX_TOOL_STEPS = 24
TOOL_BUDGET_EXHAUSTED_PROMPT = (
    "The automatic tool budget for this reply has been exhausted. Do not call any more tools. "
    "Using only the evidence already gathered in this conversation, provide the best possible "
    "concise answer, summarize what was verified, list the most likely next steps, and clearly "
    "state that the automatic tool budget was reached if the task is still incomplete."
)
THINK_BLOCK_PATTERN = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
INVOKE_BLOCK_PATTERN = re.compile(r"<invoke\b[^>]*>.*?</invoke>", re.IGNORECASE | re.DOTALL)
TOOL_CALL_BLOCK_PATTERN = re.compile(
    r"<(?:[\w-]+:)?tool_call\b[^>]*>.*?</(?:[\w-]+:)?tool_call>",
    re.IGNORECASE | re.DOTALL,
)
TOOL_CALL_TAG_PATTERN = re.compile(r"</?(?:[\w-]+:)?tool_call\b[^>]*>", re.IGNORECASE)
INVOKE_TAG_PATTERN = re.compile(r"</?invoke\b[^>]*>", re.IGNORECASE)
DEFAULT_ANTHROPIC_API_BASE_URL = "https://api.anthropic.com"


def strip_tool_protocol_markup(content: str) -> str:
    cleaned_content = content
    for pattern in (
        TOOL_CALL_BLOCK_PATTERN,
        INVOKE_BLOCK_PATTERN,
        TOOL_CALL_TAG_PATTERN,
        INVOKE_TAG_PATTERN,
    ):
        cleaned_content = pattern.sub("", cleaned_content)
    return cleaned_content.strip()


def strip_think_blocks(content: str) -> str:
    cleaned_content = THINK_BLOCK_PATTERN.sub("", content)
    cleaned_content = strip_tool_protocol_markup(cleaned_content)
    return cleaned_content.strip()


def sanitize_assistant_content(
    content: str,
    *,
    strip_thinking: bool = False,
    fallback_text: str = "",
) -> str:
    cleaned_content = strip_tool_protocol_markup(content)
    if strip_thinking:
        cleaned_content = THINK_BLOCK_PATTERN.sub("", cleaned_content)
    cleaned_content = cleaned_content.strip()
    if cleaned_content:
        return cleaned_content
    return fallback_text


class ChatRuntimeError(Exception):
    pass


class ChatRuntimeConfigurationError(ChatRuntimeError):
    pass


@dataclass(slots=True)
class ToolCallRequest:
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    mcp_server_id: str | None = None
    mcp_tool_name: str | None = None


@dataclass(slots=True)
class ToolCallResult:
    tool_name: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MCPToolBinding:
    tool_alias: str
    server_id: str
    server_name: str
    source: str
    scope: str
    transport: str
    tool_name: str
    tool_title: str | None
    tool_description: str | None
    input_schema: dict[str, object]


@dataclass(slots=True)
class ConversationMessage:
    role: MessageRole
    content: str
    attachments: list[AttachmentMetadata] = field(default_factory=list)


ToolExecutor = Callable[[ToolCallRequest], Awaitable[ToolCallResult]]
TextDeltaHandler = Callable[[str], Awaitable[None]]
SummaryHandler = Callable[[str], Awaitable[None]]
CancelledChecker = Callable[[], bool]


def _fallback_mcp_input_schema() -> dict[str, object]:
    return {"type": "object", "properties": {}, "additionalProperties": True}


def _normalize_mcp_input_schema(schema: object) -> dict[str, object]:
    if not isinstance(schema, dict):
        return _fallback_mcp_input_schema()
    if schema.get("type") != "object":
        return _fallback_mcp_input_schema()
    normalized_schema = dict(schema)
    properties = normalized_schema.get("properties")
    normalized_schema["properties"] = properties if isinstance(properties, dict) else {}
    required = normalized_schema.get("required")
    if required is not None and not isinstance(required, list):
        normalized_schema.pop("required", None)
    if "additionalProperties" not in normalized_schema:
        normalized_schema["additionalProperties"] = True
    return normalized_schema


def _normalize_mcp_tool_bindings(
    mcp_tools: list[Mapping[str, object]] | None,
) -> list[MCPToolBinding]:
    bindings: list[MCPToolBinding] = []
    for item in mcp_tools or []:
        tool_alias = item.get("tool_alias")
        server_id = item.get("server_id")
        tool_name = item.get("tool_name")
        server_name = item.get("server_name")
        source = item.get("source")
        scope = item.get("scope")
        transport = item.get("transport")
        if not all(
            isinstance(value, str) and value
            for value in (tool_alias, server_id, tool_name, server_name, source, scope, transport)
        ):
            continue
        normalized_tool_alias = str(tool_alias)
        normalized_server_id = str(server_id)
        normalized_tool_name = str(tool_name)
        normalized_server_name = str(server_name)
        normalized_source = str(source)
        normalized_scope = str(scope)
        normalized_transport = str(transport)
        tool_title = item.get("tool_title")
        tool_description = item.get("tool_description")
        bindings.append(
            MCPToolBinding(
                tool_alias=normalized_tool_alias,
                server_id=normalized_server_id,
                server_name=normalized_server_name,
                source=normalized_source,
                scope=normalized_scope,
                transport=normalized_transport,
                tool_name=normalized_tool_name,
                tool_title=tool_title if isinstance(tool_title, str) else None,
                tool_description=(tool_description if isinstance(tool_description, str) else None),
                input_schema=_normalize_mcp_input_schema(item.get("input_schema")),
            )
        )
    return bindings


def _find_mcp_tool_binding(
    tool_alias: str,
    mcp_tools: list[Mapping[str, object]] | None,
) -> MCPToolBinding | None:
    normalized_alias = tool_alias.strip()
    if not normalized_alias:
        return None
    for binding in _normalize_mcp_tool_bindings(mcp_tools):
        if binding.tool_alias == normalized_alias:
            return binding
    return None


@dataclass(slots=True)
class GenerationCallbacks:
    on_text_delta: TextDeltaHandler | None = None
    on_summary: SummaryHandler | None = None
    is_cancelled: CancelledChecker | None = None


def _append_tool_budget_exhausted_prompt(
    messages: list[dict[str, object]],
) -> list[dict[str, object]]:
    return [
        *messages,
        {
            "role": "user",
            "content": TOOL_BUDGET_EXHAUSTED_PROMPT,
        },
    ]


class ChatRuntime(Protocol):
    async def generate_reply(
        self,
        content: str,
        attachments: list[AttachmentMetadata],
        conversation_messages: list[ConversationMessage] | None = None,
        available_skills: list[SkillAgentSummaryRead] | None = None,
        mcp_tools: list[Mapping[str, object]] | None = None,
        skill_context_prompt: str | None = None,
        execute_tool: ToolExecutor | None = None,
        callbacks: GenerationCallbacks | None = None,
    ) -> str: ...


class OpenAICompatibleChatRuntime:
    def __init__(self, settings: Settings, timeout_seconds: float = 30.0) -> None:
        self._settings = settings
        self._timeout_seconds = timeout_seconds

    async def generate_reply(
        self,
        content: str,
        attachments: list[AttachmentMetadata],
        conversation_messages: list[ConversationMessage] | None = None,
        available_skills: list[SkillAgentSummaryRead] | None = None,
        mcp_tools: list[Mapping[str, object]] | None = None,
        skill_context_prompt: str | None = None,
        execute_tool: ToolExecutor | None = None,
        callbacks: GenerationCallbacks | None = None,
    ) -> str:
        api_key, base_url, model = self._require_configuration()
        endpoint = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        messages = self._build_initial_messages(
            content,
            attachments,
            available_skills or [],
            skill_context_prompt=skill_context_prompt,
            conversation_messages=conversation_messages,
        )

        for _ in range(MAX_TOOL_STEPS + 1):
            payload = self._build_payload(
                model,
                messages,
                mcp_tools=mcp_tools,
                allow_tools=execute_tool is not None,
                stream=callbacks is not None,
            )
            if callbacks is not None:
                response_payload = await self._stream_completion(
                    endpoint, headers, payload, callbacks
                )
            else:
                response_payload = await self._request_completion(endpoint, headers, payload)
            assistant_message = self._extract_message_payload(response_payload)
            text_content = self._extract_message_content(assistant_message.get("content"))
            tool_calls = self._extract_tool_calls(assistant_message, available_skills, mcp_tools)

            if tool_calls:
                if execute_tool is None:
                    raise ChatRuntimeError(
                        "LLM requested a tool call, but tool execution is unavailable."
                    )

                messages.append(self._assistant_message_for_history(assistant_message))
                for tool_call in tool_calls:
                    tool_result = await execute_tool(tool_call)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.tool_call_id,
                            "content": json.dumps(
                                {
                                    "tool": tool_result.tool_name,
                                    "payload": tool_result.payload,
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                continue

            if text_content:
                return text_content

            raise ChatRuntimeError("LLM API response did not include text content.")

        return await self._generate_tool_budget_reply(endpoint, headers, model, messages)

    def _require_configuration(self) -> tuple[str, str, str]:
        api_key = self._settings.llm_api_key
        base_url = self._settings.llm_api_base_url
        model = self._settings.llm_default_model

        if not api_key:
            raise ChatRuntimeConfigurationError("LLM API key is not configured.")

        if not base_url:
            raise ChatRuntimeConfigurationError("LLM API base URL is not configured.")

        if not model:
            raise ChatRuntimeConfigurationError("LLM default model is not configured.")

        return api_key, base_url, model

    async def _request_completion(
        self,
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> dict[str, object]:
        timeout = httpx.Timeout(self._timeout_seconds, connect=min(self._timeout_seconds, 10.0))

        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                raise ChatRuntimeError("LLM request timed out.") from exc
            except httpx.HTTPStatusError as exc:
                raise ChatRuntimeError(
                    f"LLM API request failed with status {exc.response.status_code}."
                ) from exc
            except httpx.HTTPError as exc:
                raise ChatRuntimeError("LLM API request failed.") from exc

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise ChatRuntimeError("LLM API returned invalid JSON.") from exc

        if not isinstance(response_payload, dict):
            raise ChatRuntimeError("LLM API returned an unexpected response shape.")

        return response_payload

    def _build_payload(
        self,
        model: str,
        messages: list[dict[str, object]],
        *,
        mcp_tools: list[Mapping[str, object]] | None,
        allow_tools: bool,
        stream: bool,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if allow_tools:
            payload["tools"] = self._tool_definitions(mcp_tools)
        return payload

    async def _generate_tool_budget_reply(
        self,
        endpoint: str,
        headers: dict[str, str],
        model: str,
        messages: list[dict[str, object]],
    ) -> str:
        payload = self._build_payload(
            model,
            _append_tool_budget_exhausted_prompt(messages),
            mcp_tools=None,
            allow_tools=False,
            stream=False,
        )
        response_payload = await self._request_completion(endpoint, headers, payload)
        assistant_message = self._extract_message_payload(response_payload)
        text_content = self._extract_message_content(assistant_message.get("content"))
        if text_content:
            return text_content

        raise ChatRuntimeError("LLM API exceeded the maximum number of automatic tool steps.")

    async def _stream_completion(
        self,
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, object],
        callbacks: GenerationCallbacks,
    ) -> dict[str, object]:
        timeout = httpx.Timeout(self._timeout_seconds, connect=min(self._timeout_seconds, 10.0))
        message: dict[str, object] = {"role": "assistant", "content": "", "tool_calls": []}
        tool_call_fragments: dict[int, dict[str, object]] = {}

        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with client.stream(
                    "POST", endpoint, headers=headers, json=payload
                ) as response:
                    response.raise_for_status()
                    async for raw_line in response.aiter_lines():
                        if callbacks.is_cancelled is not None and callbacks.is_cancelled():
                            raise asyncio.CancelledError

                        line = raw_line.strip()
                        if not line or not line.startswith("data:"):
                            continue

                        data = line.removeprefix("data:").strip()
                        if data == "[DONE]":
                            break

                        chunk = json.loads(data)
                        if not isinstance(chunk, dict):
                            continue

                        choices = chunk.get("choices")
                        if not isinstance(choices, list) or not choices:
                            continue

                        choice = choices[0]
                        if not isinstance(choice, dict):
                            continue

                        delta = choice.get("delta")
                        if not isinstance(delta, dict):
                            continue

                        content_delta = delta.get("content")
                        if isinstance(content_delta, str) and content_delta:
                            message["content"] = f"{message['content']}{content_delta}"
                            if callbacks.on_text_delta is not None:
                                await callbacks.on_text_delta(content_delta)

                        for raw_tool_call in delta.get("tool_calls", []):
                            if not isinstance(raw_tool_call, dict):
                                continue
                            index = raw_tool_call.get("index")
                            if not isinstance(index, int):
                                continue

                            fragment = tool_call_fragments.setdefault(
                                index,
                                {
                                    "id": raw_tool_call.get("id"),
                                    "function": {"name": "", "arguments": ""},
                                },
                            )
                            if isinstance(raw_tool_call.get("id"), str):
                                fragment["id"] = raw_tool_call["id"]
                            function_payload = raw_tool_call.get("function")
                            if isinstance(function_payload, dict):
                                function_fragment = fragment["function"]
                                if isinstance(function_fragment, dict):
                                    if isinstance(function_payload.get("name"), str):
                                        function_fragment["name"] = (
                                            f"{function_fragment['name']}{function_payload['name']}"
                                        )
                                    if isinstance(function_payload.get("arguments"), str):
                                        function_fragment["arguments"] = (
                                            f"{function_fragment['arguments']}{function_payload['arguments']}"
                                        )
            except asyncio.CancelledError:
                raise
            except httpx.TimeoutException as exc:
                raise ChatRuntimeError("LLM request timed out.") from exc
            except httpx.HTTPStatusError as exc:
                raise ChatRuntimeError(
                    f"LLM API request failed with status {exc.response.status_code}."
                ) from exc
            except httpx.HTTPError as exc:
                raise ChatRuntimeError("LLM API request failed.") from exc
            except json.JSONDecodeError as exc:
                raise ChatRuntimeError("LLM API returned invalid JSON.") from exc

        message["tool_calls"] = [
            tool_call_fragments[index] for index in sorted(tool_call_fragments)
        ]
        return {"choices": [{"message": message}]}

    @staticmethod
    def _build_initial_messages(
        content: str,
        attachments: list[AttachmentMetadata],
        available_skills: list[SkillAgentSummaryRead],
        *,
        skill_context_prompt: str | None,
        conversation_messages: list[ConversationMessage] | None,
    ) -> list[dict[str, object]]:
        assembly = build_openai_prompt_assembly(
            content=content,
            attachments=attachments,
            conversation_messages=conversation_messages,
            available_skills=available_skills,
            skill_context_prompt=skill_context_prompt,
            total_budget=12_000,
        )
        return assembly.messages

    @staticmethod
    def _format_message_content(content: str, attachments: list[AttachmentMetadata]) -> str:
        return format_message_content(content, attachments)

    @staticmethod
    def _tool_definitions(
        mcp_tools: list[Mapping[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        return [
            OpenAICompatibleChatRuntime._execute_kali_command_definition(),
            OpenAICompatibleChatRuntime._list_available_skills_definition(),
            OpenAICompatibleChatRuntime._read_skill_content_definition(),
            *OpenAICompatibleChatRuntime._mcp_tool_definitions(mcp_tools),
        ]

    @staticmethod
    def _mcp_tool_definitions(
        mcp_tools: list[Mapping[str, object]] | None,
    ) -> list[dict[str, object]]:
        definitions: list[dict[str, object]] = []
        for binding in _normalize_mcp_tool_bindings(mcp_tools):
            description = (
                binding.tool_description
                or binding.tool_title
                or f"Call MCP tool '{binding.tool_name}' on server '{binding.server_name}'."
            )
            definitions.append(
                {
                    "type": "function",
                    "function": {
                        "name": binding.tool_alias,
                        "description": description,
                        "parameters": binding.input_schema,
                    },
                }
            )
        return definitions

    @staticmethod
    def _execute_kali_command_definition() -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": "execute_kali_command",
                "description": (
                    "Run a command inside the retained Kali container for authorized defensive "
                    "security validation and return the captured stdout, stderr, exit code, and "
                    "registered artifact paths."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Shell command to execute inside the Kali runtime.",
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Optional timeout in seconds.",
                        },
                        "artifact_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Optional runtime-workspace-relative artifact paths to "
                                "register after "
                                "the command finishes."
                            ),
                        },
                    },
                    "required": ["command"],
                    "additionalProperties": False,
                },
            },
        }

    @staticmethod
    def _list_available_skills_definition() -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": "list_available_skills",
                "description": (
                    "List the currently loaded project skills with concise summaries. Use this "
                    "when the user asks which skills exist or when you need to confirm the "
                    "available catalog before choosing one."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        }

    @staticmethod
    def _read_skill_content_definition() -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": "read_skill_content",
                "description": (
                    "Read the real SKILL.md content for a loaded skill by its directory slug, "
                    "display name, or id. Use this before explaining or applying a specific "
                    "skill."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "skill_name_or_id": {
                            "type": "string",
                            "description": "Loaded skill directory slug, skill name, or skill id.",
                        }
                    },
                    "required": ["skill_name_or_id"],
                    "additionalProperties": False,
                },
            },
        }

    @staticmethod
    def _extract_message_payload(response_payload: object) -> dict[str, object]:
        if not isinstance(response_payload, dict):
            raise ChatRuntimeError("LLM API returned an unexpected response shape.")

        choices = response_payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ChatRuntimeError("LLM API response did not include any choices.")

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ChatRuntimeError("LLM API returned an invalid choice payload.")

        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise ChatRuntimeError("LLM API response did not include a message payload.")

        return message

    @staticmethod
    def _assistant_message_for_history(message: dict[str, object]) -> dict[str, object]:
        content = message.get("content", "")
        if isinstance(content, str):
            content = OpenAICompatibleChatRuntime._sanitize_assistant_content(content)
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": message.get("tool_calls", []),
        }

    @staticmethod
    def _extract_message_content(content: object) -> str | None:
        if isinstance(content, str):
            normalized_content = OpenAICompatibleChatRuntime._sanitize_assistant_content(content)
            return normalized_content or None

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if not isinstance(item, dict):
                    continue

                text = item.get("text")
                if item.get("type") == "text" and isinstance(text, str):
                    normalized_text = text.strip()
                    if normalized_text:
                        parts.append(normalized_text)

            if parts:
                return OpenAICompatibleChatRuntime._sanitize_assistant_content("\n".join(parts))

        return None

    @staticmethod
    def _sanitize_assistant_content(content: str) -> str:
        return sanitize_assistant_content(
            content,
            strip_thinking=False,
            fallback_text="模型已完成分析，但没有返回可展示的最终答复。",
        )

    @staticmethod
    def _build_skill_catalog_context(
        available_skills: list[SkillAgentSummaryRead],
    ) -> str | None:
        return render_skill_catalog_context(available_skills)

    @staticmethod
    def _find_skill_identifier(
        tool_name: str,
        available_skills: list[SkillAgentSummaryRead],
    ) -> str | None:
        normalized_tool_name = tool_name.strip().casefold()
        if not normalized_tool_name:
            return None

        for skill in available_skills:
            for candidate in (skill.id, skill.directory_name, skill.name):
                if (
                    isinstance(candidate, str)
                    and candidate.strip().casefold() == normalized_tool_name
                ):
                    return skill.directory_name or skill.name or skill.id
        return None

    @staticmethod
    def _coerce_skill_tool_call(
        *,
        tool_call_id: str,
        function_name: str,
        available_skills: list[SkillAgentSummaryRead],
    ) -> ToolCallRequest | None:
        skill_identifier = OpenAICompatibleChatRuntime._find_skill_identifier(
            function_name, available_skills
        )
        if skill_identifier is None:
            return None
        return ToolCallRequest(
            tool_call_id=tool_call_id,
            tool_name="read_skill_content",
            arguments={"skill_name_or_id": skill_identifier},
        )

    @staticmethod
    def _extract_tool_calls(
        message: dict[str, object],
        available_skills: list[SkillAgentSummaryRead] | None = None,
        mcp_tools: list[Mapping[str, object]] | None = None,
    ) -> list[ToolCallRequest]:
        raw_tool_calls = message.get("tool_calls")
        if not isinstance(raw_tool_calls, list):
            return []

        normalized_available_skills = available_skills or []
        tool_calls: list[ToolCallRequest] = []
        for raw_tool_call in raw_tool_calls:
            if not isinstance(raw_tool_call, dict):
                continue

            tool_call_id = raw_tool_call.get("id")
            function_payload = raw_tool_call.get("function")
            if not isinstance(tool_call_id, str) or not isinstance(function_payload, dict):
                continue

            function_name = function_payload.get("name")
            if not isinstance(function_name, str) or not function_name:
                raise ChatRuntimeError("LLM requested an invalid tool.")

            arguments = function_payload.get("arguments")
            parsed_arguments = OpenAICompatibleChatRuntime._parse_tool_arguments(arguments)
            if function_name == "execute_kali_command":
                tool_calls.append(
                    OpenAICompatibleChatRuntime._build_execute_kali_command_request(
                        tool_call_id,
                        parsed_arguments,
                    )
                )
                continue

            if function_name == "list_available_skills":
                if parsed_arguments:
                    raise ChatRuntimeError(
                        "LLM list_available_skills tool call included unexpected arguments."
                    )
                tool_calls.append(
                    ToolCallRequest(
                        tool_call_id=tool_call_id, tool_name=function_name, arguments={}
                    )
                )
                continue

            if function_name == "read_skill_content":
                raw_identifier = parsed_arguments.get(
                    "skill_name_or_id", parsed_arguments.get("skill_id")
                )
                if not isinstance(raw_identifier, str) or not raw_identifier.strip():
                    raise ChatRuntimeError(
                        "LLM read_skill_content tool call did not include a valid skill identifier."
                    )
                tool_calls.append(
                    ToolCallRequest(
                        tool_call_id=tool_call_id,
                        tool_name=function_name,
                        arguments={"skill_name_or_id": raw_identifier.strip()},
                    )
                )
                continue

            mcp_binding = _find_mcp_tool_binding(function_name, mcp_tools)
            if mcp_binding is not None:
                tool_calls.append(
                    ToolCallRequest(
                        tool_call_id=tool_call_id,
                        tool_name=mcp_binding.tool_alias,
                        arguments=parsed_arguments,
                        mcp_server_id=mcp_binding.server_id,
                        mcp_tool_name=mcp_binding.tool_name,
                    )
                )
                continue

            skill_tool_call = OpenAICompatibleChatRuntime._coerce_skill_tool_call(
                tool_call_id=tool_call_id,
                function_name=function_name,
                available_skills=normalized_available_skills,
            )
            if skill_tool_call is not None:
                tool_calls.append(skill_tool_call)
                continue

            raise ChatRuntimeError(f"LLM requested an unsupported tool: {function_name}.")

        return tool_calls

    @staticmethod
    def _build_execute_kali_command_request(
        tool_call_id: str,
        parsed_arguments: dict[str, Any],
    ) -> ToolCallRequest:
        command = parsed_arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ChatRuntimeError("LLM tool call did not include a valid command.")

        timeout_seconds = parsed_arguments.get("timeout_seconds")
        if timeout_seconds is not None and not isinstance(timeout_seconds, int):
            raise ChatRuntimeError("LLM tool call included an invalid timeout.")
        if isinstance(timeout_seconds, int) and timeout_seconds <= 0:
            raise ChatRuntimeError("LLM tool call included a non-positive timeout.")

        artifact_paths = parsed_arguments.get("artifact_paths")
        if artifact_paths is None:
            normalized_artifact_paths: list[str] = []
        elif isinstance(artifact_paths, list) and all(
            isinstance(item, str) for item in artifact_paths
        ):
            normalized_artifact_paths = artifact_paths
        else:
            raise ChatRuntimeError("LLM tool call included invalid artifact paths.")

        return ToolCallRequest(
            tool_call_id=tool_call_id,
            tool_name="execute_kali_command",
            arguments={
                "command": command.strip(),
                "timeout_seconds": timeout_seconds,
                "artifact_paths": normalized_artifact_paths,
            },
        )

    @staticmethod
    def _parse_tool_arguments(arguments: object) -> dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments

        if isinstance(arguments, str):
            try:
                parsed_arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise ChatRuntimeError("LLM tool call arguments were not valid JSON.") from exc

            if isinstance(parsed_arguments, dict):
                return parsed_arguments

        raise ChatRuntimeError("LLM tool call arguments had an unexpected shape.")


class AnthropicChatRuntime:
    def __init__(self, settings: Settings, timeout_seconds: float = 30.0) -> None:
        self._settings = settings
        self._timeout_seconds = timeout_seconds

    async def generate_reply(
        self,
        content: str,
        attachments: list[AttachmentMetadata],
        conversation_messages: list[ConversationMessage] | None = None,
        available_skills: list[SkillAgentSummaryRead] | None = None,
        mcp_tools: list[Mapping[str, object]] | None = None,
        skill_context_prompt: str | None = None,
        execute_tool: ToolExecutor | None = None,
        callbacks: GenerationCallbacks | None = None,
    ) -> str:
        api_key, base_url, model = self._require_configuration()
        endpoint = self._build_messages_endpoint(base_url)
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        messages = self._build_initial_messages(
            content,
            attachments,
            available_skills or [],
            skill_context_prompt=skill_context_prompt,
            conversation_messages=conversation_messages,
        )

        for _ in range(MAX_TOOL_STEPS + 1):
            payload = self._build_payload(
                model,
                messages,
                mcp_tools=mcp_tools,
                allow_tools=execute_tool is not None,
                stream=callbacks is not None,
            )
            if callbacks is not None:
                response_payload = await self._stream_completion(
                    endpoint, headers, payload, callbacks
                )
            else:
                response_payload = await self._request_completion(endpoint, headers, payload)
            text_content, tool_uses = self._extract_response_content(response_payload)

            if tool_uses:
                if execute_tool is None:
                    raise ChatRuntimeError(
                        "LLM requested a tool call, but tool execution is unavailable."
                    )

                messages.append(
                    {
                        "role": "assistant",
                        "content": response_payload.get("content", []),
                    }
                )
                tool_results: list[dict[str, object]] = []
                for tool_use in tool_uses:
                    tool_request = self._extract_tool_request_from_use(
                        tool_use, available_skills, mcp_tools
                    )
                    tool_result = await execute_tool(tool_request)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use.get("id"),
                            "content": json.dumps(
                                {
                                    "tool": tool_result.tool_name,
                                    "payload": tool_result.payload,
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                messages.append(
                    {
                        "role": "user",
                        "content": tool_results,
                    }
                )
                continue

            if text_content:
                return text_content

            raise ChatRuntimeError("LLM API response did not include text content.")

        return await self._generate_tool_budget_reply(endpoint, headers, model, messages)

    def _require_configuration(self) -> tuple[str, str, str]:
        api_key = self._settings.anthropic_api_key
        base_url = self._settings.anthropic_api_base_url or DEFAULT_ANTHROPIC_API_BASE_URL
        model = self._settings.anthropic_model

        if not api_key:
            raise ChatRuntimeConfigurationError("Anthropic API key is not configured.")

        if not model:
            raise ChatRuntimeConfigurationError("Anthropic model is not configured.")

        return api_key, base_url, model

    @staticmethod
    def _build_messages_endpoint(base_url: str) -> str:
        normalized_base_url = base_url.rstrip("/")
        if normalized_base_url.endswith("/v1/messages"):
            return normalized_base_url
        if normalized_base_url.endswith("/messages"):
            without_messages_suffix = normalized_base_url[: -len("/messages")]
            if without_messages_suffix.endswith("/v1"):
                return normalized_base_url
            return f"{without_messages_suffix}/v1/messages"
        if normalized_base_url.endswith("/v1"):
            return f"{normalized_base_url}/messages"
        return f"{normalized_base_url}/v1/messages"

    async def _request_completion(
        self,
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, object],
    ) -> dict[str, object]:
        timeout = httpx.Timeout(self._timeout_seconds, connect=min(self._timeout_seconds, 10.0))

        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
            except httpx.TimeoutException as exc:
                raise ChatRuntimeError("LLM request timed out.") from exc
            except httpx.HTTPStatusError as exc:
                raise ChatRuntimeError(
                    f"LLM API request failed with status {exc.response.status_code}."
                ) from exc
            except httpx.HTTPError as exc:
                raise ChatRuntimeError("LLM API request failed.") from exc

        try:
            response_payload = response.json()
        except ValueError as exc:
            raise ChatRuntimeError("LLM API returned invalid JSON.") from exc

        if not isinstance(response_payload, dict):
            raise ChatRuntimeError("LLM API returned an unexpected response shape.")

        return response_payload

    def _build_payload(
        self,
        model: str,
        messages: list[dict[str, object]],
        *,
        mcp_tools: list[Mapping[str, object]] | None,
        allow_tools: bool,
        stream: bool,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": model,
            "messages": messages,
            "max_tokens": 4096,
            "system": SYSTEM_PROMPT,
            "stream": stream,
        }
        if allow_tools:
            payload["tools"] = self._tool_definitions(mcp_tools)
        return payload

    async def _generate_tool_budget_reply(
        self,
        endpoint: str,
        headers: dict[str, str],
        model: str,
        messages: list[dict[str, object]],
    ) -> str:
        payload = self._build_payload(
            model,
            _append_tool_budget_exhausted_prompt(messages),
            mcp_tools=None,
            allow_tools=False,
            stream=False,
        )
        response_payload = await self._request_completion(endpoint, headers, payload)
        text_content, tool_uses = self._extract_response_content(response_payload)
        if text_content and not tool_uses:
            return text_content

        raise ChatRuntimeError("LLM API exceeded the maximum number of automatic tool steps.")

    async def _stream_completion(
        self,
        endpoint: str,
        headers: dict[str, str],
        payload: dict[str, object],
        callbacks: GenerationCallbacks,
    ) -> dict[str, object]:
        timeout = httpx.Timeout(self._timeout_seconds, connect=min(self._timeout_seconds, 10.0))
        text_parts: list[str] = []
        tool_use_fragments: dict[int, dict[str, object]] = {}
        tool_use_input_fragments: dict[int, list[str]] = {}

        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with client.stream(
                    "POST", endpoint, headers=headers, json=payload
                ) as response:
                    response.raise_for_status()
                    event_name = ""
                    async for raw_line in response.aiter_lines():
                        if callbacks.is_cancelled is not None and callbacks.is_cancelled():
                            raise asyncio.CancelledError

                        line = raw_line.strip()
                        if not line:
                            event_name = ""
                            continue
                        if line.startswith("event:"):
                            event_name = line.removeprefix("event:").strip()
                            continue
                        if not line.startswith("data:"):
                            continue

                        data = line.removeprefix("data:").strip()
                        if data == "[DONE]":
                            break

                        event_payload = json.loads(data)
                        if not isinstance(event_payload, dict):
                            continue

                        if event_name == "content_block_delta":
                            delta = event_payload.get("delta")
                            if not isinstance(delta, dict):
                                continue
                            delta_type = delta.get("type")
                            if delta_type in {"text_delta", "text"}:
                                text = delta.get("text")
                                if isinstance(text, str) and text:
                                    text_parts.append(text)
                                    if callbacks.on_text_delta is not None:
                                        await callbacks.on_text_delta(text)
                                continue
                            if delta_type == "input_json_delta":
                                index = event_payload.get("index")
                                partial_json = delta.get("partial_json")
                                if isinstance(index, int) and isinstance(partial_json, str):
                                    fragments = tool_use_input_fragments.setdefault(index, [])
                                    fragments.append(partial_json)
                                continue

                        if event_name == "content_block_start":
                            index = event_payload.get("index")
                            content_block = event_payload.get("content_block")
                            if not isinstance(index, int) or not isinstance(content_block, dict):
                                continue
                            if content_block.get("type") != "tool_use":
                                continue
                            tool_use_fragments[index] = {
                                "type": "tool_use",
                                "id": content_block.get("id"),
                                "name": content_block.get("name"),
                                "input": {},
                            }
            except asyncio.CancelledError:
                raise
            except httpx.TimeoutException as exc:
                raise ChatRuntimeError("LLM request timed out.") from exc
            except httpx.HTTPStatusError as exc:
                raise ChatRuntimeError(
                    f"LLM API request failed with status {exc.response.status_code}."
                ) from exc
            except httpx.HTTPError as exc:
                raise ChatRuntimeError("LLM API request failed.") from exc
            except json.JSONDecodeError as exc:
                raise ChatRuntimeError("LLM API returned invalid JSON.") from exc

        content_blocks: list[dict[str, object]] = []
        if text_parts:
            content_blocks.append({"type": "text", "text": "".join(text_parts)})

        for index in sorted(tool_use_fragments):
            tool_use = dict(tool_use_fragments[index])
            input_payload: dict[str, object] = {}
            raw_input = "".join(tool_use_input_fragments.get(index, []))
            if raw_input:
                try:
                    parsed_input = json.loads(raw_input)
                    if isinstance(parsed_input, dict):
                        input_payload = parsed_input
                except json.JSONDecodeError:
                    input_payload = {}
            tool_use["input"] = input_payload
            content_blocks.append(tool_use)

        return {"content": content_blocks}

    @staticmethod
    def _build_initial_messages(
        content: str,
        attachments: list[AttachmentMetadata],
        available_skills: list[SkillAgentSummaryRead],
        *,
        skill_context_prompt: str | None,
        conversation_messages: list[ConversationMessage] | None,
    ) -> list[dict[str, object]]:
        assembly = build_anthropic_prompt_assembly(
            content=content,
            attachments=attachments,
            conversation_messages=conversation_messages,
            available_skills=available_skills,
            skill_context_prompt=skill_context_prompt,
            total_budget=12_000,
        )
        return assembly.messages

    @staticmethod
    def _tool_definitions(
        mcp_tools: list[Mapping[str, object]] | None = None,
    ) -> list[dict[str, object]]:
        return [
            AnthropicChatRuntime._execute_kali_command_definition(),
            AnthropicChatRuntime._list_available_skills_definition(),
            AnthropicChatRuntime._read_skill_content_definition(),
            *AnthropicChatRuntime._mcp_tool_definitions(mcp_tools),
        ]

    @staticmethod
    def _mcp_tool_definitions(
        mcp_tools: list[Mapping[str, object]] | None,
    ) -> list[dict[str, object]]:
        definitions: list[dict[str, object]] = []
        for binding in _normalize_mcp_tool_bindings(mcp_tools):
            description = (
                binding.tool_description
                or binding.tool_title
                or f"Call MCP tool '{binding.tool_name}' on server '{binding.server_name}'."
            )
            definitions.append(
                {
                    "name": binding.tool_alias,
                    "description": description,
                    "input_schema": binding.input_schema,
                }
            )
        return definitions

    @staticmethod
    def _execute_kali_command_definition() -> dict[str, object]:
        return {
            "name": "execute_kali_command",
            "description": (
                "Run a command inside the retained Kali container for authorized defensive "
                "security validation and return the captured stdout, stderr, exit code, and "
                "registered artifact paths."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute inside the Kali runtime.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Optional timeout in seconds.",
                    },
                    "artifact_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional runtime-workspace-relative artifact paths to "
                            "register after the command finishes."
                        ),
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        }

    @staticmethod
    def _list_available_skills_definition() -> dict[str, object]:
        return {
            "name": "list_available_skills",
            "description": (
                "List the currently loaded project skills with concise summaries. Use this "
                "when the user asks which skills exist or when you need to confirm the "
                "available catalog before choosing one."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }

    @staticmethod
    def _read_skill_content_definition() -> dict[str, object]:
        return {
            "name": "read_skill_content",
            "description": (
                "Read the real SKILL.md content for a loaded skill by its directory slug, "
                "display name, or id. Use this before explaining or applying a specific "
                "skill."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "skill_name_or_id": {
                        "type": "string",
                        "description": "Loaded skill directory slug, skill name, or skill id.",
                    }
                },
                "required": ["skill_name_or_id"],
                "additionalProperties": False,
            },
        }

    @staticmethod
    def _extract_response_content(
        response_payload: Mapping[str, object],
    ) -> tuple[str | None, list[dict[str, object]]]:
        content = response_payload.get("content")
        if not isinstance(content, list):
            raise ChatRuntimeError("LLM API response content is not a list.")

        text_parts: list[str] = []
        tool_uses: list[dict[str, object]] = []

        for block in content:
            if not isinstance(block, dict):
                continue

            block_type = block.get("type")
            if block_type == "text":
                text = block.get("text")
                if isinstance(text, str):
                    normalized = AnthropicChatRuntime._sanitize_assistant_content(text)
                    if normalized:
                        text_parts.append(normalized)
            elif block_type == "tool_use":
                tool_uses.append(block)

        text_content = "\n".join(text_parts) if text_parts else None
        return text_content, tool_uses

    @staticmethod
    def _sanitize_assistant_content(content: str) -> str:
        return sanitize_assistant_content(content, strip_thinking=False, fallback_text="")

    @staticmethod
    def _extract_tool_request_from_use(
        tool_use: dict[str, object],
        available_skills: list[SkillAgentSummaryRead] | None = None,
        mcp_tools: list[Mapping[str, object]] | None = None,
    ) -> ToolCallRequest:
        tool_use_id = tool_use.get("id")
        tool_name = tool_use.get("name")
        input_data = tool_use.get("input")

        if not isinstance(tool_use_id, str):
            raise ChatRuntimeError("LLM tool use did not include a valid ID.")
        if not isinstance(tool_name, str) or not tool_name:
            raise ChatRuntimeError("LLM requested an invalid tool.")

        parsed_arguments = AnthropicChatRuntime._parse_tool_arguments(input_data)

        if tool_name == "execute_kali_command":
            return AnthropicChatRuntime._build_execute_kali_command_request(
                tool_use_id, parsed_arguments
            )
        elif tool_name == "list_available_skills":
            if parsed_arguments:
                raise ChatRuntimeError(
                    "LLM list_available_skills tool call included unexpected arguments."
                )
            return ToolCallRequest(tool_call_id=tool_use_id, tool_name=tool_name, arguments={})
        elif tool_name == "read_skill_content":
            raw_identifier = parsed_arguments.get(
                "skill_name_or_id", parsed_arguments.get("skill_id")
            )
            if not isinstance(raw_identifier, str) or not raw_identifier.strip():
                raise ChatRuntimeError(
                    "LLM read_skill_content tool call did not include a valid skill identifier."
                )
            return ToolCallRequest(
                tool_call_id=tool_use_id,
                tool_name=tool_name,
                arguments={"skill_name_or_id": raw_identifier.strip()},
            )
        else:
            mcp_binding = _find_mcp_tool_binding(tool_name, mcp_tools)
            if mcp_binding is not None:
                return ToolCallRequest(
                    tool_call_id=tool_use_id,
                    tool_name=mcp_binding.tool_alias,
                    arguments=parsed_arguments,
                    mcp_server_id=mcp_binding.server_id,
                    mcp_tool_name=mcp_binding.tool_name,
                )
            skill_tool_call = OpenAICompatibleChatRuntime._coerce_skill_tool_call(
                tool_call_id=tool_use_id,
                function_name=tool_name,
                available_skills=available_skills or [],
            )
            if skill_tool_call is not None:
                return skill_tool_call
            raise ChatRuntimeError(f"LLM requested an unsupported tool: {tool_name}.")

    @staticmethod
    def _build_execute_kali_command_request(
        tool_use_id: str,
        parsed_arguments: dict[str, Any],
    ) -> ToolCallRequest:
        command = parsed_arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ChatRuntimeError("LLM tool call did not include a valid command.")

        timeout_seconds = parsed_arguments.get("timeout_seconds")
        if timeout_seconds is not None and not isinstance(timeout_seconds, int):
            raise ChatRuntimeError("LLM tool call included an invalid timeout.")
        if isinstance(timeout_seconds, int) and timeout_seconds <= 0:
            raise ChatRuntimeError("LLM tool call included a non-positive timeout.")

        artifact_paths = parsed_arguments.get("artifact_paths")
        if artifact_paths is None:
            normalized_artifact_paths: list[str] = []
        elif isinstance(artifact_paths, list) and all(
            isinstance(item, str) for item in artifact_paths
        ):
            normalized_artifact_paths = artifact_paths
        else:
            raise ChatRuntimeError("LLM tool call included invalid artifact paths.")

        return ToolCallRequest(
            tool_call_id=tool_use_id,
            tool_name="execute_kali_command",
            arguments={
                "command": command.strip(),
                "timeout_seconds": timeout_seconds,
                "artifact_paths": normalized_artifact_paths,
            },
        )

    @staticmethod
    def _parse_tool_arguments(arguments: object) -> dict[str, Any]:
        if isinstance(arguments, dict):
            return arguments

        if isinstance(arguments, str):
            try:
                parsed_arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise ChatRuntimeError("LLM tool call arguments were not valid JSON.") from exc

            if isinstance(parsed_arguments, dict):
                return parsed_arguments

        raise ChatRuntimeError("LLM tool call arguments had an unexpected shape.")

    @staticmethod
    def _build_skill_catalog_context(
        available_skills: list[SkillAgentSummaryRead],
    ) -> str | None:
        if not available_skills:
            return None

        lines = [
            "Loaded Skills Catalog "
            "(summary only; use read_skill_content for the real SKILL.md body):"
        ]
        for skill in available_skills:
            description = " ".join(skill.description.split()) or "No description provided."
            if len(description) > 140:
                description = description[:137].rstrip() + "..."
            label = skill.directory_name
            if skill.name != skill.directory_name:
                label = f"{skill.directory_name} (name: {skill.name})"
            lines.append(f"- {label}: {description}")

        lines.append(
            "If the user asks to list skills, explain a skill, or use a skill, "
            "call the skills tools before asking broad clarification questions. "
            "Skill names in this catalog are reference entries, not callable tools. "
            "Fixed callable tool names are execute_kali_command, list_available_skills, "
            "and read_skill_content. Additional callable MCP tool aliases may appear in the "
            "capability context."
        )
        return "\n".join(lines)


def get_chat_runtime() -> ChatRuntime:
    settings = get_settings()
    provider = settings.llm_provider
    timeout_seconds = float(settings.llm_request_timeout_seconds)
    if provider == "anthropic":
        return AnthropicChatRuntime(settings, timeout_seconds=timeout_seconds)
    else:
        return OpenAICompatibleChatRuntime(settings, timeout_seconds=timeout_seconds)
