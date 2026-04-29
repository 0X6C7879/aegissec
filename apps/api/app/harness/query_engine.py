from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, cast

from app.db.models import AttachmentMetadata, SkillAgentSummaryRead

from .messages import (
    ChatRuntimeError,
    ConversationMessage,
    GenerationCallbacks,
    ProviderTurnResult,
    QueryUsage,
    ToolCallRequest,
    ToolCallResult,
)

_TOOL_RESULT_HISTORY_MAX_CHARS = 4_000
_TOOL_RESULT_TEXT_PREVIEW_CHARS = 500
_TOOL_RESULT_LIST_PREVIEW_ITEMS = 10


class ToolResultSummarizer:
    """Compresses tool results to fit within context budget."""

    MAX_CHARS = _TOOL_RESULT_HISTORY_MAX_CHARS
    PREVIEW_CHARS = 500
    SCAN_TOOL_NAMES = {"nmap", "masscan", "nuclei", "nikto", "gobuster", "ffuf", "dirsearch"}

    @staticmethod
    def summarize(result_text: str, tool_name: str = "") -> str:
        """Summarize a tool result, preserving key findings."""
        if len(result_text) <= ToolResultSummarizer.MAX_CHARS:
            return result_text

        lines = result_text.splitlines()
        if ToolResultSummarizer._is_scan_tool(tool_name):
            scan_summary = ToolResultSummarizer._summarize_scan_output(result_text, lines)
            if len(scan_summary) <= ToolResultSummarizer.MAX_CHARS:
                return scan_summary

        head = result_text[: ToolResultSummarizer.PREVIEW_CHARS]
        tail = result_text[-ToolResultSummarizer.PREVIEW_CHARS :]
        omitted = len(result_text) - (ToolResultSummarizer.PREVIEW_CHARS * 2)
        return f"{head}\n\n[... {omitted} chars omitted ...]\n\n{tail}"

    @staticmethod
    def _summarize_scan_output(text: str, lines: list[str]) -> str:
        """Summarize scan tool output preserving findings."""
        if len(lines) <= 100:
            return text

        head_lines = lines[:50]
        tail_lines = lines[-50:]
        omitted = len(lines) - 100

        head = "\n".join(head_lines)
        tail = "\n".join(tail_lines)

        return f"{head}\n\n[... {omitted} lines omitted ...]\n\n{tail}"

    @staticmethod
    def _is_scan_tool(tool_name: str) -> bool:
        normalized_tool_name = tool_name.lower()
        return any(
            scan_tool in normalized_tool_name
            for scan_tool in ToolResultSummarizer.SCAN_TOOL_NAMES
        )


def _assistant_tool_call_ids(assistant_payload: Mapping[str, Any]) -> list[str]:
    raw_tool_calls = assistant_payload.get("tool_calls")
    if raw_tool_calls is None:
        return []
    if not isinstance(raw_tool_calls, list):
        raise ChatRuntimeError("Assistant tool_calls payload is invalid for history replay.")

    tool_call_ids: list[str] = []
    for raw_tool_call in raw_tool_calls:
        if not isinstance(raw_tool_call, Mapping):
            raise ChatRuntimeError("Assistant tool_calls payload is invalid for history replay.")
        tool_call_id = raw_tool_call.get("id")
        if not isinstance(tool_call_id, str) or not tool_call_id.strip():
            raise ChatRuntimeError("Assistant tool_calls payload is missing a valid tool_call id.")
        tool_call_ids.append(tool_call_id)
    return tool_call_ids


def _anthropic_tool_use_ids(assistant_payload: Mapping[str, Any]) -> list[str]:
    raw_content = assistant_payload.get("content")
    if not isinstance(raw_content, list):
        raise ChatRuntimeError("Anthropic assistant content is invalid for tool replay.")

    tool_use_ids: list[str] = []
    for block in raw_content:
        if not isinstance(block, Mapping) or block.get("type") != "tool_use":
            continue
        tool_use_id = block.get("id")
        if not isinstance(tool_use_id, str) or not tool_use_id.strip():
            raise ChatRuntimeError("Anthropic assistant tool_use block is missing a valid id.")
        tool_use_ids.append(tool_use_id)
    return tool_use_ids


def _summarize_tool_payload_strings(
    payload: Mapping[str, Any], *, tool_name: str
) -> dict[str, Any]:
    summarized_payload: dict[str, Any] = {}
    for field_name, field_value in payload.items():
        if isinstance(field_value, str):
            summarized_payload[str(field_name)] = ToolResultSummarizer.summarize(
                field_value,
                tool_name=tool_name,
            )
        else:
            summarized_payload[str(field_name)] = field_value
    return summarized_payload


def _preview_text(value: Any) -> tuple[str | None, int | None, bool]:
    if not isinstance(value, str):
        return None, None, False
    truncated = len(value) > _TOOL_RESULT_TEXT_PREVIEW_CHARS
    return value[:_TOOL_RESULT_TEXT_PREVIEW_CHARS], len(value), truncated


def _summarize_tool_payload(
    payload: Mapping[str, Any], *, safe_summary: str | None
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "truncated": True,
        "keys": sorted(str(key) for key in payload.keys()),
    }
    if safe_summary:
        summary["summary"] = safe_summary
    for field_name in ("status", "command", "exit_code"):
        field_value = payload.get(field_name)
        if isinstance(field_value, str | int | float | bool) or field_value is None:
            if field_value is not None:
                summary[field_name] = field_value

    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        summary["artifacts"] = [str(item) for item in artifacts[:_TOOL_RESULT_LIST_PREVIEW_ITEMS]]
        summary["artifact_count"] = len(artifacts)

    stdout_preview, stdout_chars, stdout_truncated = _preview_text(payload.get("stdout"))
    if stdout_preview is not None:
        summary["stdout_preview"] = stdout_preview
        summary["stdout_chars"] = stdout_chars
        summary["stdout_truncated"] = stdout_truncated

    stderr_preview, stderr_chars, stderr_truncated = _preview_text(payload.get("stderr"))
    if stderr_preview is not None:
        summary["stderr_preview"] = stderr_preview
        summary["stderr_chars"] = stderr_chars
        summary["stderr_truncated"] = stderr_truncated

    return summary


def _render_tool_result_history_content(tool_result: ToolCallResult) -> str:
    history_payload = {"tool": tool_result.tool_name, "payload": tool_result.payload}
    serialized_payload = json.dumps(history_payload, ensure_ascii=False)
    if len(serialized_payload) <= _TOOL_RESULT_HISTORY_MAX_CHARS:
        return serialized_payload

    if isinstance(tool_result.payload, Mapping):
        summarized_history_payload: dict[str, Any] = {
            "tool": tool_result.tool_name,
            "payload": _summarize_tool_payload_strings(
                tool_result.payload,
                tool_name=tool_result.tool_name,
            ),
            "payload_summary": {
                "original_char_length": len(serialized_payload),
                **_summarize_tool_payload(
                    tool_result.payload,
                    safe_summary=tool_result.safe_summary,
                ),
            },
        }
        if tool_result.safe_summary:
            summarized_history_payload["safe_summary"] = tool_result.safe_summary
        summarized_serialized_payload = json.dumps(summarized_history_payload, ensure_ascii=False)
        if len(summarized_serialized_payload) <= _TOOL_RESULT_HISTORY_MAX_CHARS:
            return summarized_serialized_payload

    return ToolResultSummarizer.summarize(serialized_payload, tool_name=tool_result.tool_name)


class BaseQueryEngine(ABC):
    def __init__(
        self,
        *,
        messages: list[dict[str, Any]],
        model_name: str,
        system_prompt: str | None,
        max_turns: int,
        max_budget_cycles: int = 1,
        session_state: Any | None = None,
        compact_service: Any | None = None,
    ) -> None:
        self.messages = messages
        self.model_name = model_name
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.max_budget_cycles = max_budget_cycles
        self.usage = QueryUsage()
        self.pending_continuation = False
        self.session_state = session_state
        self.compact_service = compact_service

    async def submit_message(
        self,
        *,
        execute_tool: Callable[[object], Awaitable[object]] | None,
        callbacks: GenerationCallbacks | None,
    ) -> str:
        from .query_loop import QueryLoop

        return await QueryLoop(
            max_turns=self.max_turns,
            max_budget_cycles=self.max_budget_cycles,
        ).run(
            self,
            execute_tool=execute_tool,
            callbacks=callbacks,
        )

    async def continue_pending(
        self,
        *,
        execute_tool: Callable[[object], Awaitable[object]] | None,
        callbacks: GenerationCallbacks | None,
    ) -> str:
        if not self.pending_continuation:
            raise ChatRuntimeError("No pending continuation is available for this query engine.")
        return await self.submit_message(execute_tool=execute_tool, callbacks=callbacks)

    @abstractmethod
    async def request_turn(
        self,
        *,
        allow_tools: bool,
        callbacks: GenerationCallbacks | None,
    ) -> ProviderTurnResult:
        raise NotImplementedError

    @abstractmethod
    def append_tool_results(
        self,
        *,
        assistant_payload: dict[str, Any],
        tool_calls: Sequence[ToolCallRequest],
        tool_results: Sequence[ToolCallResult],
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def generate_tool_budget_reply(
        self,
        *,
        callbacks: GenerationCallbacks | None,
    ) -> str:
        raise NotImplementedError

    async def generate_tool_budget_reflection(
        self,
        *,
        callbacks: GenerationCallbacks | None,
        cycle_index: int,
        max_cycles: int,
        recent_attempts_summary: str = "",
    ) -> str | None:
        del callbacks, cycle_index, max_cycles, recent_attempts_summary
        return None

    def maybe_auto_compact(self) -> None:
        if self.compact_service is None:
            return
        self.messages = self.compact_service.maybe_compact(
            messages=self.messages,
            session_state=self.session_state,
            render_compact_message=self.render_compact_message,
            turn_count=self.usage.model_turns,
        )

    @abstractmethod
    def render_compact_message(self, compact_fragment: str) -> dict[str, Any]:
        raise NotImplementedError

    def append_context_injections(self, injections: Sequence[str]) -> None:
        for injection in injections:
            normalized_injection = injection.strip()
            if not normalized_injection:
                continue
            self.messages.append(
                self.render_compact_message(
                    "Additional operator context injected during the active run:\n"
                    f"{normalized_injection}"
                )
            )

    def append_budget_reflection(
        self,
        reflection_text: str,
        *,
        cycle_index: int,
        max_cycles: int,
    ) -> None:
        normalized_reflection = reflection_text.strip()
        if not normalized_reflection:
            return
        self.messages.append(
            self.render_compact_message(
                "Autonomous reflection after exhausting the current tool phase "
                f"({cycle_index}/{max_cycles}).\n"
                "Use this to avoid repeated dead ends and choose the next best action.\n"
                f"{normalized_reflection}"
            )
        )

    @abstractmethod
    def append_assistant_response_to_history(self, assistant_payload: dict[str, Any]) -> None:
        raise NotImplementedError

    def dequeue_synthetic_tool_call(self) -> ToolCallRequest | None:
        if self.session_state is None:
            return None
        if getattr(self.session_state, "slash_action_consumed", False):
            return None
        raw_slash_action = getattr(self.session_state, "slash_action", None)
        if not isinstance(raw_slash_action, Mapping):
            return None
        raw_invocation = raw_slash_action.get("invocation")
        if not isinstance(raw_invocation, Mapping):
            return None
        action_id = raw_slash_action.get("id")
        if not isinstance(action_id, str) or not action_id.strip():
            return None

        normalized_invocation = dict(raw_invocation)
        normalized_invocation["tool_call_id"] = self._synthetic_tool_call_id(action_id)
        tool_call = ToolCallRequest.model_validate(normalized_invocation)
        self.session_state.slash_action_consumed = True
        return tool_call

    @staticmethod
    def _synthetic_tool_call_id(action_id: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", action_id).strip("-")
        return f"slash-{normalized or 'action'}"

    @abstractmethod
    def build_synthetic_assistant_payload(
        self,
        tool_calls: Sequence[ToolCallRequest],
    ) -> dict[str, Any]:
        raise NotImplementedError


class OpenAIQueryEngine(BaseQueryEngine):
    def __init__(
        self,
        *,
        provider: Any,
        content: str,
        attachments: Sequence[AttachmentMetadata],
        conversation_messages: Sequence[ConversationMessage] | None,
        available_skills: Sequence[SkillAgentSummaryRead],
        mcp_tools: Sequence[Mapping[str, Any]] | None,
        skill_context_prompt: str | None,
        max_turns: int,
        system_prompt: str,
        max_budget_cycles: int = 1,
        session_state: Any | None = None,
        compact_service: Any | None = None,
    ) -> None:
        api_key, base_url, model = provider._require_configuration()
        self._provider = provider
        self._api_key = api_key
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._model = model
        self._available_skills = list(available_skills)
        self._mcp_tools = list(mcp_tools or [])
        initial_messages = provider._build_initial_messages(
            content,
            attachments,
            conversation_messages=conversation_messages,
            available_skills=available_skills,
            skill_context_prompt=skill_context_prompt,
        )
        super().__init__(
            messages=initial_messages,
            model_name=model,
            system_prompt=system_prompt,
            max_turns=max_turns,
            max_budget_cycles=max_budget_cycles,
            session_state=session_state,
            compact_service=compact_service,
        )

    async def request_turn(
        self,
        *,
        allow_tools: bool,
        callbacks: GenerationCallbacks | None,
    ) -> ProviderTurnResult:
        stream = callbacks is not None
        payload = self._provider._build_payload(
            self._model,
            self.messages,
            mcp_tools=self._mcp_tools,
            allow_tools=allow_tools,
            stream=stream,
        )
        if stream:
            response_payload = await self._provider._stream_completion(
                self._endpoint,
                self._headers,
                payload,
                callbacks=callbacks,
            )
        else:
            response_payload = await self._provider._request_completion(
                self._endpoint,
                self._headers,
                payload,
            )
        assistant_payload = self._provider._extract_message_payload(response_payload)
        text_content = self._provider._extract_message_content(assistant_payload.get("content"))
        tool_calls = self._provider._extract_tool_calls(
            assistant_payload,
            self._available_skills,
            self._mcp_tools,
        )
        return ProviderTurnResult(
            assistant_payload=assistant_payload,
            text_content=text_content,
            tool_calls=tool_calls,
        )

    def append_tool_results(
        self,
        *,
        assistant_payload: dict[str, Any],
        tool_calls: Sequence[ToolCallRequest],
        tool_results: Sequence[ToolCallResult],
    ) -> None:
        if len(tool_calls) != len(tool_results):
            raise ChatRuntimeError("Tool result count mismatch during assistant history replay.")

        assistant_tool_call_ids = _assistant_tool_call_ids(assistant_payload)
        requested_tool_call_ids = [tool_call.tool_call_id for tool_call in tool_calls]
        if assistant_tool_call_ids != requested_tool_call_ids:
            raise ChatRuntimeError(
                "Assistant tool_call ids do not match tool execution results during history replay."
            )

        for tool_call, tool_result in zip(tool_calls, tool_results, strict=True):
            result_tool_call_id = tool_result.tool_call_id or tool_call.tool_call_id
            if result_tool_call_id != tool_call.tool_call_id:
                raise ChatRuntimeError(
                    "Tool result tool_call_id does not match the originating assistant tool call."
                )

        self.append_assistant_response_to_history(assistant_payload)
        for tool_call, tool_result in zip(tool_calls, tool_results, strict=True):
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.tool_call_id,
                    "name": tool_call.tool_name,
                    "content": _render_tool_result_history_content(tool_result),
                }
            )

    def append_assistant_response_to_history(self, assistant_payload: dict[str, Any]) -> None:
        self.messages.append(self._provider._assistant_message_for_history(assistant_payload))

    def build_synthetic_assistant_payload(
        self,
        tool_calls: Sequence[ToolCallRequest],
    ) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": tool_call.tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_call.tool_name,
                        "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                    },
                }
                for tool_call in tool_calls
            ],
        }

    async def generate_tool_budget_reply(
        self,
        *,
        callbacks: GenerationCallbacks | None,
    ) -> str:
        return cast(
            str,
            await self._provider._generate_tool_budget_reply(
                self._endpoint,
                self._headers,
                self._model,
                self.messages,
            ),
        )

    async def generate_tool_budget_reflection(
        self,
        *,
        callbacks: GenerationCallbacks | None,
        cycle_index: int,
        max_cycles: int,
        recent_attempts_summary: str = "",
    ) -> str | None:
        del callbacks
        return cast(
            str,
            await self._provider._generate_tool_budget_reflection(
                self._endpoint,
                self._headers,
                self._model,
                self.messages,
                cycle_index=cycle_index,
                max_cycles=max_cycles,
                recent_attempts_summary=recent_attempts_summary,
            ),
        )

    def render_compact_message(self, compact_fragment: str) -> dict[str, Any]:
        return {"role": "user", "content": compact_fragment}


class AnthropicQueryEngine(BaseQueryEngine):
    def __init__(
        self,
        *,
        provider: Any,
        content: str,
        attachments: Sequence[AttachmentMetadata],
        conversation_messages: Sequence[ConversationMessage] | None,
        available_skills: Sequence[SkillAgentSummaryRead],
        mcp_tools: Sequence[Mapping[str, Any]] | None,
        skill_context_prompt: str | None,
        max_turns: int,
        system_prompt: str,
        max_budget_cycles: int = 1,
        session_state: Any | None = None,
        compact_service: Any | None = None,
    ) -> None:
        api_key, base_url, model = provider._require_configuration()
        self._provider = provider
        self._api_key = api_key
        self._endpoint = provider._build_messages_endpoint(base_url)
        self._headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        self._model = model
        self._available_skills = list(available_skills)
        self._mcp_tools = list(mcp_tools or [])
        initial_messages = provider._build_initial_messages(
            content,
            attachments,
            conversation_messages=conversation_messages,
            available_skills=available_skills,
            skill_context_prompt=skill_context_prompt,
        )
        super().__init__(
            messages=initial_messages,
            model_name=model,
            system_prompt=system_prompt,
            max_turns=max_turns,
            max_budget_cycles=max_budget_cycles,
            session_state=session_state,
            compact_service=compact_service,
        )

    async def request_turn(
        self,
        *,
        allow_tools: bool,
        callbacks: GenerationCallbacks | None,
    ) -> ProviderTurnResult:
        payload = self._provider._build_payload(
            self._model,
            self.messages,
            mcp_tools=self._mcp_tools,
            allow_tools=allow_tools,
            stream=callbacks is not None,
        )
        if callbacks is not None:
            response_payload = await self._provider._stream_completion(
                self._endpoint,
                self._headers,
                payload,
                callbacks=callbacks,
            )
        else:
            response_payload = await self._provider._request_completion(
                self._endpoint,
                self._headers,
                payload,
            )
        text_content, tool_uses = self._provider._extract_response_content(response_payload)
        tool_calls = [
            self._provider._extract_tool_request_from_use(
                tool_use,
                self._available_skills,
                self._mcp_tools,
            )
            for tool_use in tool_uses
        ]
        return ProviderTurnResult(
            assistant_payload=response_payload,
            text_content=text_content,
            tool_calls=tool_calls,
        )

    def append_tool_results(
        self,
        *,
        assistant_payload: dict[str, Any],
        tool_calls: Sequence[ToolCallRequest],
        tool_results: Sequence[ToolCallResult],
    ) -> None:
        if len(tool_calls) != len(tool_results):
            raise ChatRuntimeError("Tool result count mismatch during Anthropic history replay.")

        assistant_tool_use_ids = _anthropic_tool_use_ids(assistant_payload)
        requested_tool_call_ids = [tool_call.tool_call_id for tool_call in tool_calls]
        if assistant_tool_use_ids != requested_tool_call_ids:
            raise ChatRuntimeError(
                "Anthropic assistant tool_use ids do not match tool execution results "
                "during history replay."
            )

        for tool_call, tool_result in zip(tool_calls, tool_results, strict=True):
            result_tool_call_id = tool_result.tool_call_id or tool_call.tool_call_id
            if result_tool_call_id != tool_call.tool_call_id:
                raise ChatRuntimeError(
                    "Tool result tool_call_id does not match the originating Anthropic tool call."
                )

        self.append_assistant_response_to_history(assistant_payload)
        user_content = []
        for tool_call, tool_result in zip(tool_calls, tool_results, strict=True):
            user_content.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call.tool_call_id,
                    "content": _render_tool_result_history_content(tool_result),
                }
            )
        self.messages.append({"role": "user", "content": user_content})

    def append_assistant_response_to_history(self, assistant_payload: dict[str, Any]) -> None:
        assistant_content = assistant_payload.get("content")
        if not isinstance(assistant_content, list):
            raise ChatRuntimeError("Anthropic response content must be a list.")
        self.messages.append({"role": "assistant", "content": assistant_content})

    def build_synthetic_assistant_payload(
        self,
        tool_calls: Sequence[ToolCallRequest],
    ) -> dict[str, Any]:
        return {
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_call.tool_call_id,
                    "name": tool_call.tool_name,
                    "input": dict(tool_call.arguments),
                }
                for tool_call in tool_calls
            ]
        }

    async def generate_tool_budget_reply(
        self,
        *,
        callbacks: GenerationCallbacks | None,
    ) -> str:
        return cast(
            str,
            await self._provider._generate_tool_budget_reply(
                self._endpoint,
                self._headers,
                self._model,
                self.messages,
            ),
        )

    async def generate_tool_budget_reflection(
        self,
        *,
        callbacks: GenerationCallbacks | None,
        cycle_index: int,
        max_cycles: int,
        recent_attempts_summary: str = "",
    ) -> str | None:
        del callbacks
        return cast(
            str,
            await self._provider._generate_tool_budget_reflection(
                self._endpoint,
                self._headers,
                self._model,
                self.messages,
                cycle_index=cycle_index,
                max_cycles=max_cycles,
                recent_attempts_summary=recent_attempts_summary,
            ),
        )

    def render_compact_message(self, compact_fragment: str) -> dict[str, Any]:
        return {"role": "user", "content": compact_fragment}
