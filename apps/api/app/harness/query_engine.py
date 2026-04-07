from __future__ import annotations

import json
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


class BaseQueryEngine(ABC):
    def __init__(
        self,
        *,
        messages: list[dict[str, Any]],
        model_name: str,
        system_prompt: str | None,
        max_turns: int,
        session_state: Any | None = None,
        compact_service: Any | None = None,
    ) -> None:
        self.messages = messages
        self.model_name = model_name
        self.system_prompt = system_prompt
        self.max_turns = max_turns
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

        return await QueryLoop(max_turns=self.max_turns).run(
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
        self.messages.append(self._provider._assistant_message_for_history(assistant_payload))
        for tool_call, tool_result in zip(tool_calls, tool_results, strict=False):
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.tool_call_id,
                    "content": json.dumps(
                        {"tool": tool_result.tool_name, "payload": tool_result.payload},
                        ensure_ascii=False,
                    ),
                }
            )

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
        assistant_content = assistant_payload.get("content")
        if not isinstance(assistant_content, list):
            raise ChatRuntimeError("Anthropic response content must be a list.")
        self.messages.append({"role": "assistant", "content": assistant_content})
        user_content = []
        for tool_call, tool_result in zip(tool_calls, tool_results, strict=False):
            user_content.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call.tool_call_id,
                    "content": json.dumps(
                        {"tool": tool_result.tool_name, "payload": tool_result.payload},
                        ensure_ascii=False,
                    ),
                }
            )
        self.messages.append({"role": "user", "content": user_content})

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

    def render_compact_message(self, compact_fragment: str) -> dict[str, Any]:
        return {"role": "user", "content": compact_fragment}
