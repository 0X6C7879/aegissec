from __future__ import annotations

import asyncio
import importlib
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

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
from app.services.llm_rate_control import compute_retry_delay_seconds, get_llm_rate_controller

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
HTTP_ERROR_BODY_PREVIEW_LIMIT = 1500
_FIXED_NO_ARGUMENT_TOOL_NAMES = {"list_available_skills", "list_terminal_sessions"}


def strip_tool_protocol_markup(content: str) -> str:
    cleaned_content = TOOL_CALL_BLOCK_PATTERN.sub("", content)
    cleaned_content = INVOKE_BLOCK_PATTERN.sub("", cleaned_content)
    cleaned_content = TOOL_CALL_TAG_PATTERN.sub("", cleaned_content)
    cleaned_content = INVOKE_TAG_PATTERN.sub("", cleaned_content)
    return cleaned_content


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
    cleaned_content = content
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
    tool_call_id: str | None = None
    safe_summary: str | None = None


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
ContextInjectionConsumer = Callable[[], Awaitable[list[str]]]
ContextInjectionHandler = Callable[[list[str]], Awaitable[None]]


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
    consume_context_injections: ContextInjectionConsumer | None = None
    on_context_injection_applied: ContextInjectionHandler | None = None


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


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


def _extract_openai_usage_snapshot(response_payload: Mapping[str, object]) -> UsageSnapshot:
    raw_usage = response_payload.get("usage")
    if not isinstance(raw_usage, dict):
        return UsageSnapshot()

    prompt_tokens = raw_usage.get("prompt_tokens")
    completion_tokens = raw_usage.get("completion_tokens")
    total_tokens = raw_usage.get("total_tokens")
    return UsageSnapshot(
        input_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
        output_tokens=completion_tokens if isinstance(completion_tokens, int) else None,
        total_tokens=total_tokens if isinstance(total_tokens, int) else None,
    )


def _extract_anthropic_usage_snapshot(response_payload: Mapping[str, object]) -> UsageSnapshot:
    raw_usage = response_payload.get("usage")
    if not isinstance(raw_usage, dict):
        return UsageSnapshot()

    input_tokens = raw_usage.get("input_tokens")
    output_tokens = raw_usage.get("output_tokens")
    total_tokens = None
    if isinstance(input_tokens, int) and isinstance(output_tokens, int):
        total_tokens = input_tokens + output_tokens
    return UsageSnapshot(
        input_tokens=input_tokens if isinstance(input_tokens, int) else None,
        output_tokens=output_tokens if isinstance(output_tokens, int) else None,
        total_tokens=total_tokens,
    )


class ChatRuntime(Protocol):
    async def generate_reply(
        self,
        content: str,
        attachments: list[AttachmentMetadata],
        conversation_messages: list[ConversationMessage] | None = None,
        available_skills: list[SkillAgentSummaryRead] | None = None,
        mcp_tools: list[Mapping[str, object]] | None = None,
        skill_context_prompt: str | None = None,
        harness_state: object | None = None,
        execute_tool: ToolExecutor | None = None,
        callbacks: GenerationCallbacks | None = None,
    ) -> str: ...


class OpenAICompatibleChatRuntime:
    def __init__(self, settings: Settings, timeout_seconds: float = 30.0) -> None:
        self._settings = settings
        self._timeout_seconds = timeout_seconds
        self._rate_controller = get_llm_rate_controller(settings)

    async def generate_reply(
        self,
        content: str,
        attachments: list[AttachmentMetadata],
        conversation_messages: list[ConversationMessage] | None = None,
        available_skills: list[SkillAgentSummaryRead] | None = None,
        mcp_tools: list[Mapping[str, object]] | None = None,
        skill_context_prompt: str | None = None,
        harness_state: object | None = None,
        execute_tool: ToolExecutor | None = None,
        callbacks: GenerationCallbacks | None = None,
    ) -> str:
        harness_messages = importlib.import_module("app.harness.messages")
        harness_compact = importlib.import_module("app.harness.compact")
        harness_memory = importlib.import_module("app.harness.memory")
        harness_query_engine = importlib.import_module("app.harness.query_engine")
        harness_runtime = importlib.import_module("app.harness.runtime")
        harness_state_module = importlib.import_module("app.harness.state")
        HarnessChatRuntimeConfigurationError = harness_messages.ChatRuntimeConfigurationError
        HarnessChatRuntimeError = harness_messages.ChatRuntimeError
        HarnessCompactService = harness_compact.HarnessCompactService
        HarnessMemoryService = harness_memory.HarnessMemoryService
        OpenAIQueryEngine = harness_query_engine.OpenAIQueryEngine
        HarnessRuntime = harness_runtime.HarnessRuntime
        HarnessSessionState = harness_state_module.HarnessSessionState

        session_state = harness_state if isinstance(harness_state, HarnessSessionState) else None
        compact_service = HarnessCompactService(memory_service=HarnessMemoryService())

        query_engine = OpenAIQueryEngine(
            provider=self,
            content=content,
            attachments=attachments,
            conversation_messages=conversation_messages,
            available_skills=available_skills or [],
            mcp_tools=mcp_tools,
            skill_context_prompt=skill_context_prompt,
            max_turns=MAX_TOOL_STEPS + 1,
            system_prompt=SYSTEM_PROMPT,
            session_state=session_state,
            compact_service=compact_service,
        )
        try:
            return cast(
                str,
                await HarnessRuntime(query_engine=query_engine).generate_reply(
                    execute_tool=execute_tool,
                    callbacks=callbacks,
                ),
            )
        except HarnessChatRuntimeConfigurationError as exc:
            raise ChatRuntimeConfigurationError(str(exc)) from exc
        except HarnessChatRuntimeError as exc:
            raise ChatRuntimeError(str(exc)) from exc

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
        previous_delay: float | None = None

        for attempt in range(self._settings.llm_rate_limit_max_retries + 1):
            lease = await self._rate_controller.acquire(
                payload,
                max_output_tokens=self._settings.llm_max_output_tokens,
            )
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(endpoint, headers=headers, json=payload)
                    response.raise_for_status()
                response_payload = response.json()
                if not isinstance(response_payload, dict):
                    raise ChatRuntimeError("LLM API returned an unexpected response shape.")
            except httpx.TimeoutException as exc:
                await self._rate_controller.finalize(lease, rate_limited=False)
                raise ChatRuntimeError("LLM request timed out.") from exc
            except httpx.HTTPStatusError as exc:
                is_rate_limit = exc.response.status_code == 429
                await self._rate_controller.finalize(lease, rate_limited=is_rate_limit)
                if is_rate_limit and attempt < self._settings.llm_rate_limit_max_retries:
                    delay_seconds = compute_retry_delay_seconds(
                        headers=exc.response.headers,
                        attempt=attempt + 1,
                        previous_delay=previous_delay,
                        config=self._rate_controller.config,
                    )
                    previous_delay = delay_seconds
                    await self._rate_controller.note_backoff(delay_seconds)
                    await asyncio.sleep(delay_seconds)
                    continue
                if is_rate_limit:
                    raise ChatRuntimeError("LLM API rate limit exceeded after retries.") from exc
                raise ChatRuntimeError(
                    await OpenAICompatibleChatRuntime._format_status_error_message(exc)
                ) from exc
            except httpx.HTTPError as exc:
                await self._rate_controller.finalize(lease, rate_limited=False)
                raise ChatRuntimeError("LLM API request failed.") from exc
            except ValueError as exc:
                await self._rate_controller.finalize(lease, rate_limited=False)
                raise ChatRuntimeError("LLM API returned invalid JSON.") from exc
            except ChatRuntimeError:
                await self._rate_controller.finalize(lease, rate_limited=False)
                raise

            usage = _extract_openai_usage_snapshot(response_payload)
            await self._rate_controller.finalize(
                lease,
                rate_limited=False,
                actual_input_tokens=usage.input_tokens,
                actual_output_tokens=usage.output_tokens,
                actual_total_tokens=usage.total_tokens,
            )
            return response_payload

        raise ChatRuntimeError("LLM API rate limit exceeded after retries.")

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
            "max_tokens": self._settings.llm_max_output_tokens,
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
        previous_delay: float | None = None

        for attempt in range(self._settings.llm_rate_limit_max_retries + 1):
            message: dict[str, object] = {"role": "assistant", "content": "", "tool_calls": []}
            tool_call_fragments: dict[int, dict[str, object]] = {}
            lease = await self._rate_controller.acquire(
                payload,
                max_output_tokens=self._settings.llm_max_output_tokens,
            )
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
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

                            content_delta = (
                                OpenAICompatibleChatRuntime._coerce_stream_content_delta_text(
                                    delta.get("content")
                                )
                            )
                            if content_delta:
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
                                        "type": (
                                            raw_tool_call.get("type")
                                            if isinstance(raw_tool_call.get("type"), str)
                                            else "function"
                                        ),
                                        "function": {"name": "", "arguments": ""},
                                    },
                                )
                                if isinstance(raw_tool_call.get("id"), str):
                                    fragment["id"] = raw_tool_call["id"]
                                if isinstance(raw_tool_call.get("type"), str):
                                    fragment["type"] = raw_tool_call["type"]
                                function_payload = raw_tool_call.get("function")
                                if isinstance(function_payload, dict):
                                    function_fragment = fragment["function"]
                                    if isinstance(function_fragment, dict):
                                        if isinstance(function_payload.get("name"), str):
                                            function_fragment["name"] = (
                                                f"{function_fragment['name']}"
                                                f"{function_payload['name']}"
                                            )
                                        if isinstance(function_payload.get("arguments"), str):
                                            function_fragment["arguments"] = (
                                                f"{function_fragment['arguments']}"
                                                f"{function_payload['arguments']}"
                                            )
            except asyncio.CancelledError:
                await self._rate_controller.finalize(lease, rate_limited=False)
                raise
            except httpx.TimeoutException as exc:
                await self._rate_controller.finalize(lease, rate_limited=False)
                raise ChatRuntimeError("LLM request timed out.") from exc
            except httpx.HTTPStatusError as exc:
                is_rate_limit = exc.response.status_code == 429
                await self._rate_controller.finalize(lease, rate_limited=is_rate_limit)
                if is_rate_limit and attempt < self._settings.llm_rate_limit_max_retries:
                    delay_seconds = compute_retry_delay_seconds(
                        headers=exc.response.headers,
                        attempt=attempt + 1,
                        previous_delay=previous_delay,
                        config=self._rate_controller.config,
                    )
                    previous_delay = delay_seconds
                    await self._rate_controller.note_backoff(delay_seconds)
                    await asyncio.sleep(delay_seconds)
                    continue
                if is_rate_limit:
                    raise ChatRuntimeError("LLM API rate limit exceeded after retries.") from exc
                raise ChatRuntimeError(
                    await OpenAICompatibleChatRuntime._format_status_error_message(exc)
                ) from exc
            except httpx.HTTPError as exc:
                await self._rate_controller.finalize(lease, rate_limited=False)
                raise ChatRuntimeError("LLM API request failed.") from exc
            except json.JSONDecodeError as exc:
                await self._rate_controller.finalize(lease, rate_limited=False)
                raise ChatRuntimeError("LLM API returned invalid JSON.") from exc

            await self._rate_controller.finalize(lease, rate_limited=False)
            message["tool_calls"] = [
                tool_call_fragments[index] for index in sorted(tool_call_fragments)
            ]
            return {"choices": [{"message": message}]}

        raise ChatRuntimeError("LLM API rate limit exceeded after retries.")

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
        build_default_tool_registry = importlib.import_module(
            "app.harness.tools.defaults"
        ).build_default_tool_registry

        return cast(
            list[dict[str, object]],
            build_default_tool_registry(mcp_tools=mcp_tools).to_openai_tools_schema(),
        )

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
        raw_tool_calls = message.get("tool_calls")
        tool_calls = OpenAICompatibleChatRuntime._canonicalize_tool_calls_for_history(
            raw_tool_calls
        )
        has_tool_calls = len(tool_calls) > 0
        content = message.get("content", "")
        if isinstance(content, str):
            if has_tool_calls:
                sanitized_content = sanitize_assistant_content(content, fallback_text="")
                content = sanitized_content or None
            else:
                content = OpenAICompatibleChatRuntime._sanitize_assistant_content(content)
        history_message: dict[str, object] = {
            "role": "assistant",
            "content": content,
        }
        if isinstance(raw_tool_calls, list):
            history_message["tool_calls"] = tool_calls
        return history_message

    @staticmethod
    def _canonicalize_tool_calls_for_history(tool_calls: object) -> list[dict[str, object]]:
        if not isinstance(tool_calls, list):
            return []

        canonical_tool_calls: list[dict[str, object]] = []
        for raw_tool_call in tool_calls:
            if not isinstance(raw_tool_call, dict):
                continue

            tool_call_id = raw_tool_call.get("id")
            if not isinstance(tool_call_id, str) or not tool_call_id.strip():
                raise ChatRuntimeError(
                    "LLM assistant tool_calls entry is missing a valid id for history replay."
                )

            function_payload = raw_tool_call.get("function")
            if not isinstance(function_payload, dict):
                raise ChatRuntimeError(
                    "LLM assistant tool_calls entry is missing a valid function payload for "
                    "history replay."
                )

            function_name = function_payload.get("name")
            if not isinstance(function_name, str) or not function_name.strip():
                raise ChatRuntimeError(
                    "LLM assistant tool_calls entry is missing a valid function name for "
                    "history replay."
                )

            tool_call_type = raw_tool_call.get("type")
            canonical_type = (
                tool_call_type.strip()
                if isinstance(tool_call_type, str) and tool_call_type.strip()
                else "function"
            )

            canonical_tool_calls.append(
                {
                    "id": tool_call_id,
                    "type": canonical_type,
                    "function": {
                        "name": function_name.strip(),
                        "arguments": OpenAICompatibleChatRuntime._canonicalize_tool_arguments(
                            function_payload.get("arguments")
                        ),
                    },
                }
            )

        return canonical_tool_calls

    @staticmethod
    def _canonicalize_tool_arguments(arguments: object) -> str:
        if isinstance(arguments, str):
            return arguments
        if isinstance(arguments, dict | list):
            return json.dumps(arguments, ensure_ascii=False)
        raise ChatRuntimeError(
            "LLM assistant tool_calls entry has unsupported function.arguments for history replay."
        )

    @staticmethod
    async def _format_status_error_message(exc: httpx.HTTPStatusError) -> str:
        message = f"LLM API request failed with status {exc.response.status_code}."
        response_text = await OpenAICompatibleChatRuntime._response_body_excerpt(exc.response)
        if response_text is None:
            return message
        return f"{message} Response body: {response_text}"

    @staticmethod
    async def _response_body_excerpt(response: httpx.Response) -> str | None:
        try:
            response_text = response.text
        except httpx.ResponseNotRead:
            try:
                await response.aread()
                response_text = response.text
            except (httpx.StreamError, httpx.HTTPError):
                return None
        except httpx.StreamError:
            return None

        normalized_text = response_text.strip()
        if not normalized_text:
            return None
        if len(normalized_text) <= HTTP_ERROR_BODY_PREVIEW_LIMIT:
            return normalized_text
        return f"{normalized_text[:HTTP_ERROR_BODY_PREVIEW_LIMIT]}..."

    @staticmethod
    def _extract_text_fragment(item: object) -> str | None:
        if isinstance(item, str):
            return item

        if not isinstance(item, dict):
            return None

        for key in ("text", "thinking", "reasoning"):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value

        return None

    @staticmethod
    def _coerce_stream_content_delta_text(content_delta: object) -> str:
        fragment = OpenAICompatibleChatRuntime._extract_text_fragment(content_delta)
        if fragment is not None:
            return fragment

        if not isinstance(content_delta, list):
            return ""

        parts: list[str] = []
        for item in content_delta:
            item_fragment = OpenAICompatibleChatRuntime._extract_text_fragment(item)
            if item_fragment is not None:
                parts.append(item_fragment)

        return "".join(parts)

    @staticmethod
    def _extract_message_content(content: object) -> str | None:
        if isinstance(content, str):
            normalized_content = OpenAICompatibleChatRuntime._sanitize_assistant_content(content)
            return normalized_content or None

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                fragment = OpenAICompatibleChatRuntime._extract_text_fragment(item)
                if fragment is not None:
                    parts.append(fragment)

            if parts:
                return OpenAICompatibleChatRuntime._sanitize_assistant_content("\n".join(parts))

        return None

    @staticmethod
    def _sanitize_assistant_content(content: str) -> str:
        return sanitize_assistant_content(
            content,
            strip_thinking=False,
            fallback_text="",
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
            tool_name="execute_skill",
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

            if function_name in _FIXED_NO_ARGUMENT_TOOL_NAMES:
                if parsed_arguments:
                    raise ChatRuntimeError(
                        f"LLM {function_name} tool call included unexpected arguments."
                    )
                tool_calls.append(
                    ToolCallRequest(
                        tool_call_id=tool_call_id, tool_name=function_name, arguments={}
                    )
                )
                continue

            if function_name == "create_terminal_session":
                tool_calls.append(
                    OpenAICompatibleChatRuntime._build_create_terminal_session_request(
                        tool_call_id,
                        parsed_arguments,
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

            if function_name == "execute_skill":
                raw_identifier = parsed_arguments.get(
                    "skill_name_or_id", parsed_arguments.get("skill_id")
                )
                if not isinstance(raw_identifier, str) or not raw_identifier.strip():
                    raise ChatRuntimeError(
                        "LLM execute_skill tool call did not include a valid skill identifier."
                    )
                tool_calls.append(
                    ToolCallRequest(
                        tool_call_id=tool_call_id,
                        tool_name=function_name,
                        arguments={"skill_name_or_id": raw_identifier.strip()},
                    )
                )
                continue

            if function_name == "execute_terminal_command":
                tool_calls.append(
                    OpenAICompatibleChatRuntime._build_execute_terminal_command_request(
                        tool_call_id,
                        parsed_arguments,
                    )
                )
                continue

            if function_name == "read_terminal_buffer":
                tool_calls.append(
                    OpenAICompatibleChatRuntime._build_read_terminal_buffer_request(
                        tool_call_id,
                        parsed_arguments,
                    )
                )
                continue

            if function_name == "stop_terminal_job":
                tool_calls.append(
                    OpenAICompatibleChatRuntime._build_stop_terminal_job_request(
                        tool_call_id,
                        parsed_arguments,
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
    def _build_create_terminal_session_request(
        tool_call_id: str,
        parsed_arguments: dict[str, Any],
    ) -> ToolCallRequest:
        payload: dict[str, Any] = {}
        for key in ("title", "shell", "cwd"):
            value = parsed_arguments.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                raise ChatRuntimeError(f"LLM {tool_call_id} included an invalid {key} value.")
            payload[key] = value
        metadata = parsed_arguments.get("metadata")
        if metadata is None:
            payload["metadata"] = {}
        elif isinstance(metadata, dict):
            payload["metadata"] = metadata
        else:
            raise ChatRuntimeError("LLM tool call included invalid terminal metadata.")
        return ToolCallRequest(
            tool_call_id=tool_call_id,
            tool_name="create_terminal_session",
            arguments=payload,
        )

    @staticmethod
    def _build_execute_terminal_command_request(
        tool_call_id: str,
        parsed_arguments: dict[str, Any],
    ) -> ToolCallRequest:
        terminal_id = parsed_arguments.get("terminal_id")
        if not isinstance(terminal_id, str) or not terminal_id.strip():
            raise ChatRuntimeError("LLM terminal tool call did not include a valid terminal_id.")
        request = OpenAICompatibleChatRuntime._build_execute_kali_command_request(
            tool_call_id,
            parsed_arguments,
        )
        return ToolCallRequest(
            tool_call_id=tool_call_id,
            tool_name="execute_terminal_command",
            arguments={
                "terminal_id": terminal_id.strip(),
                **request.arguments,
                "detach": bool(parsed_arguments.get("detach", False)),
            },
        )

    @staticmethod
    def _build_read_terminal_buffer_request(
        tool_call_id: str,
        parsed_arguments: dict[str, Any],
    ) -> ToolCallRequest:
        terminal_id = parsed_arguments.get("terminal_id")
        job_id = parsed_arguments.get("job_id")
        if not isinstance(terminal_id, str) or not terminal_id.strip():
            raise ChatRuntimeError("LLM terminal buffer call did not include a valid terminal_id.")
        if not isinstance(job_id, str) or not job_id.strip():
            raise ChatRuntimeError("LLM terminal buffer call did not include a valid job_id.")
        stream = parsed_arguments.get("stream", "stdout")
        if not isinstance(stream, str) or stream not in {"stdout", "stderr"}:
            raise ChatRuntimeError("LLM terminal buffer call included an invalid stream.")
        lines = parsed_arguments.get("lines", 200)
        if not isinstance(lines, int) or lines <= 0:
            raise ChatRuntimeError("LLM terminal buffer call included an invalid line count.")
        return ToolCallRequest(
            tool_call_id=tool_call_id,
            tool_name="read_terminal_buffer",
            arguments={
                "terminal_id": terminal_id.strip(),
                "job_id": job_id.strip(),
                "stream": stream,
                "lines": lines,
            },
        )

    @staticmethod
    def _build_stop_terminal_job_request(
        tool_call_id: str,
        parsed_arguments: dict[str, Any],
    ) -> ToolCallRequest:
        job_id = parsed_arguments.get("job_id")
        if not isinstance(job_id, str) or not job_id.strip():
            raise ChatRuntimeError("LLM stop_terminal_job call did not include a valid job_id.")
        return ToolCallRequest(
            tool_call_id=tool_call_id,
            tool_name="stop_terminal_job",
            arguments={"job_id": job_id.strip()},
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
        self._rate_controller = get_llm_rate_controller(settings)

    async def generate_reply(
        self,
        content: str,
        attachments: list[AttachmentMetadata],
        conversation_messages: list[ConversationMessage] | None = None,
        available_skills: list[SkillAgentSummaryRead] | None = None,
        mcp_tools: list[Mapping[str, object]] | None = None,
        skill_context_prompt: str | None = None,
        harness_state: object | None = None,
        execute_tool: ToolExecutor | None = None,
        callbacks: GenerationCallbacks | None = None,
    ) -> str:
        harness_messages = importlib.import_module("app.harness.messages")
        harness_compact = importlib.import_module("app.harness.compact")
        harness_memory = importlib.import_module("app.harness.memory")
        harness_query_engine = importlib.import_module("app.harness.query_engine")
        harness_runtime = importlib.import_module("app.harness.runtime")
        harness_state_module = importlib.import_module("app.harness.state")
        HarnessChatRuntimeConfigurationError = harness_messages.ChatRuntimeConfigurationError
        HarnessChatRuntimeError = harness_messages.ChatRuntimeError
        HarnessCompactService = harness_compact.HarnessCompactService
        HarnessMemoryService = harness_memory.HarnessMemoryService
        AnthropicQueryEngine = harness_query_engine.AnthropicQueryEngine
        HarnessRuntime = harness_runtime.HarnessRuntime
        HarnessSessionState = harness_state_module.HarnessSessionState

        session_state = harness_state if isinstance(harness_state, HarnessSessionState) else None
        compact_service = HarnessCompactService(memory_service=HarnessMemoryService())

        query_engine = AnthropicQueryEngine(
            provider=self,
            content=content,
            attachments=attachments,
            conversation_messages=conversation_messages,
            available_skills=available_skills or [],
            mcp_tools=mcp_tools,
            skill_context_prompt=skill_context_prompt,
            max_turns=MAX_TOOL_STEPS + 1,
            system_prompt=SYSTEM_PROMPT,
            session_state=session_state,
            compact_service=compact_service,
        )
        try:
            return cast(
                str,
                await HarnessRuntime(query_engine=query_engine).generate_reply(
                    execute_tool=execute_tool,
                    callbacks=callbacks,
                ),
            )
        except HarnessChatRuntimeConfigurationError as exc:
            raise ChatRuntimeConfigurationError(str(exc)) from exc
        except HarnessChatRuntimeError as exc:
            raise ChatRuntimeError(str(exc)) from exc

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
        previous_delay: float | None = None

        for attempt in range(self._settings.llm_rate_limit_max_retries + 1):
            lease = await self._rate_controller.acquire(
                payload,
                max_output_tokens=self._settings.llm_max_output_tokens,
            )
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(endpoint, headers=headers, json=payload)
                    response.raise_for_status()
                response_payload = response.json()
                if not isinstance(response_payload, dict):
                    raise ChatRuntimeError("LLM API returned an unexpected response shape.")
            except httpx.TimeoutException as exc:
                await self._rate_controller.finalize(lease, rate_limited=False)
                raise ChatRuntimeError("LLM request timed out.") from exc
            except httpx.HTTPStatusError as exc:
                is_rate_limit = exc.response.status_code == 429
                await self._rate_controller.finalize(lease, rate_limited=is_rate_limit)
                if is_rate_limit and attempt < self._settings.llm_rate_limit_max_retries:
                    delay_seconds = compute_retry_delay_seconds(
                        headers=exc.response.headers,
                        attempt=attempt + 1,
                        previous_delay=previous_delay,
                        config=self._rate_controller.config,
                    )
                    previous_delay = delay_seconds
                    await self._rate_controller.note_backoff(delay_seconds)
                    await asyncio.sleep(delay_seconds)
                    continue
                if is_rate_limit:
                    raise ChatRuntimeError("LLM API rate limit exceeded after retries.") from exc
                raise ChatRuntimeError(
                    f"LLM API request failed with status {exc.response.status_code}."
                ) from exc
            except httpx.HTTPError as exc:
                await self._rate_controller.finalize(lease, rate_limited=False)
                raise ChatRuntimeError("LLM API request failed.") from exc
            except ValueError as exc:
                await self._rate_controller.finalize(lease, rate_limited=False)
                raise ChatRuntimeError("LLM API returned invalid JSON.") from exc
            except ChatRuntimeError:
                await self._rate_controller.finalize(lease, rate_limited=False)
                raise

            usage = _extract_anthropic_usage_snapshot(response_payload)
            await self._rate_controller.finalize(
                lease,
                rate_limited=False,
                actual_input_tokens=usage.input_tokens,
                actual_output_tokens=usage.output_tokens,
                actual_total_tokens=usage.total_tokens,
            )
            return response_payload

        raise ChatRuntimeError("LLM API rate limit exceeded after retries.")

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
            "max_tokens": self._settings.llm_max_output_tokens,
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
        previous_delay: float | None = None

        for attempt in range(self._settings.llm_rate_limit_max_retries + 1):
            text_parts: list[str] = []
            tool_use_fragments: dict[int, dict[str, object]] = {}
            tool_use_input_fragments: dict[int, list[str]] = {}
            lease = await self._rate_controller.acquire(
                payload,
                max_output_tokens=self._settings.llm_max_output_tokens,
            )
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
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
                                if delta_type in {
                                    "text_delta",
                                    "text",
                                    "thinking_delta",
                                    "thinking",
                                }:
                                    text = delta.get("text")
                                    if not isinstance(text, str):
                                        text = delta.get("thinking")
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
                                if not isinstance(index, int) or not isinstance(
                                    content_block, dict
                                ):
                                    continue

                                content_block_type = content_block.get("type")
                                if content_block_type in {"text", "thinking"}:
                                    initial_text = content_block.get("text")
                                    if not isinstance(initial_text, str):
                                        initial_text = content_block.get("thinking")
                                    if isinstance(initial_text, str) and initial_text:
                                        text_parts.append(initial_text)
                                        if callbacks.on_text_delta is not None:
                                            await callbacks.on_text_delta(initial_text)
                                    continue

                                if content_block_type != "tool_use":
                                    continue
                                tool_use_fragments[index] = {
                                    "type": "tool_use",
                                    "id": content_block.get("id"),
                                    "name": content_block.get("name"),
                                    "input": {},
                                }
            except asyncio.CancelledError:
                await self._rate_controller.finalize(lease, rate_limited=False)
                raise
            except httpx.TimeoutException as exc:
                await self._rate_controller.finalize(lease, rate_limited=False)
                raise ChatRuntimeError("LLM request timed out.") from exc
            except httpx.HTTPStatusError as exc:
                is_rate_limit = exc.response.status_code == 429
                await self._rate_controller.finalize(lease, rate_limited=is_rate_limit)
                if is_rate_limit and attempt < self._settings.llm_rate_limit_max_retries:
                    delay_seconds = compute_retry_delay_seconds(
                        headers=exc.response.headers,
                        attempt=attempt + 1,
                        previous_delay=previous_delay,
                        config=self._rate_controller.config,
                    )
                    previous_delay = delay_seconds
                    await self._rate_controller.note_backoff(delay_seconds)
                    await asyncio.sleep(delay_seconds)
                    continue
                if is_rate_limit:
                    raise ChatRuntimeError("LLM API rate limit exceeded after retries.") from exc
                raise ChatRuntimeError(
                    f"LLM API request failed with status {exc.response.status_code}."
                ) from exc
            except httpx.HTTPError as exc:
                await self._rate_controller.finalize(lease, rate_limited=False)
                raise ChatRuntimeError("LLM API request failed.") from exc
            except json.JSONDecodeError as exc:
                await self._rate_controller.finalize(lease, rate_limited=False)
                raise ChatRuntimeError("LLM API returned invalid JSON.") from exc

            await self._rate_controller.finalize(lease, rate_limited=False)
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

        raise ChatRuntimeError("LLM API rate limit exceeded after retries.")

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
        build_default_tool_registry = importlib.import_module(
            "app.harness.tools.defaults"
        ).build_default_tool_registry

        return cast(
            list[dict[str, object]],
            build_default_tool_registry(mcp_tools=mcp_tools).to_anthropic_tools_schema(),
        )

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
            if block_type in {"text", "thinking", "redacted_thinking"}:
                text = block.get("text")
                if not isinstance(text, str):
                    text = block.get("thinking")
                if not isinstance(text, str):
                    data = block.get("data")
                    if isinstance(data, str):
                        text = data
                    elif isinstance(data, dict | list):
                        text = json.dumps(data, ensure_ascii=False)
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
        elif tool_name in _FIXED_NO_ARGUMENT_TOOL_NAMES:
            if parsed_arguments:
                raise ChatRuntimeError(f"LLM {tool_name} tool call included unexpected arguments.")
            return ToolCallRequest(tool_call_id=tool_use_id, tool_name=tool_name, arguments={})
        elif tool_name == "create_terminal_session":
            return OpenAICompatibleChatRuntime._build_create_terminal_session_request(
                tool_use_id, parsed_arguments
            )
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
        elif tool_name == "execute_skill":
            raw_identifier = parsed_arguments.get(
                "skill_name_or_id", parsed_arguments.get("skill_id")
            )
            if not isinstance(raw_identifier, str) or not raw_identifier.strip():
                raise ChatRuntimeError(
                    "LLM execute_skill tool call did not include a valid skill identifier."
                )
            return ToolCallRequest(
                tool_call_id=tool_use_id,
                tool_name=tool_name,
                arguments={"skill_name_or_id": raw_identifier.strip()},
            )
        elif tool_name == "execute_terminal_command":
            return OpenAICompatibleChatRuntime._build_execute_terminal_command_request(
                tool_use_id, parsed_arguments
            )
        elif tool_name == "read_terminal_buffer":
            return OpenAICompatibleChatRuntime._build_read_terminal_buffer_request(
                tool_use_id, parsed_arguments
            )
        elif tool_name == "stop_terminal_job":
            return OpenAICompatibleChatRuntime._build_stop_terminal_job_request(
                tool_use_id, parsed_arguments
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
            "(summary only; use execute_skill to apply a skill or "
            "read_skill_content for the real SKILL.md body):"
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
            "Skill names in this catalog are reference entries unless coerced into "
            "execute_skill by the runtime. Fixed callable tool names are "
            "execute_kali_command, list_available_skills, execute_skill, read_skill_content, "
            "create_terminal_session, list_terminal_sessions, execute_terminal_command, "
            "read_terminal_buffer, and stop_terminal_job. Additional callable MCP tool aliases "
            "may appear in the "
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
