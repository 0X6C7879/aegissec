from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.settings import Settings, get_settings
from app.db.models import AttachmentMetadata

SYSTEM_PROMPT = (
    "You are assisting an authorized defensive security research workflow. "
    "Reply in the user's language. Keep answers concise, evidence-oriented, and within the "
    "user's stated scope. If shell-based verification or tool output would materially improve "
    "accuracy, call the execute_kali_command tool automatically instead of asking the user to "
    "run commands manually. After tool execution, summarize what happened clearly."
)

MAX_TOOL_STEPS = 3
THINK_BLOCK_PATTERN = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)


class ChatRuntimeError(Exception):
    pass


class ChatRuntimeConfigurationError(ChatRuntimeError):
    pass


@dataclass(slots=True)
class ToolCallRequest:
    tool_call_id: str
    command: str
    timeout_seconds: int | None = None
    artifact_paths: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ToolCallResult:
    status: str
    exit_code: int | None
    stdout: str
    stderr: str
    artifacts: list[str] = field(default_factory=list)


ToolExecutor = Callable[[ToolCallRequest], Awaitable[ToolCallResult]]


class ChatRuntime(Protocol):
    async def generate_reply(
        self,
        content: str,
        attachments: list[AttachmentMetadata],
        execute_tool: ToolExecutor | None = None,
    ) -> str: ...


class OpenAICompatibleChatRuntime:
    def __init__(self, settings: Settings, timeout_seconds: float = 30.0) -> None:
        self._settings = settings
        self._timeout_seconds = timeout_seconds

    async def generate_reply(
        self,
        content: str,
        attachments: list[AttachmentMetadata],
        execute_tool: ToolExecutor | None = None,
    ) -> str:
        api_key, base_url, model = self._require_configuration()
        endpoint = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        messages = self._build_initial_messages(content, attachments)

        for _ in range(MAX_TOOL_STEPS + 1):
            payload = self._build_payload(model, messages, allow_tools=execute_tool is not None)
            response_payload = await self._request_completion(endpoint, headers, payload)
            assistant_message = self._extract_message_payload(response_payload)
            text_content = self._extract_message_content(assistant_message.get("content"))
            tool_calls = self._extract_tool_calls(assistant_message)

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
                                    "command": tool_call.command,
                                    "status": tool_result.status,
                                    "exit_code": tool_result.exit_code,
                                    "stdout": tool_result.stdout,
                                    "stderr": tool_result.stderr,
                                    "artifacts": tool_result.artifacts,
                                },
                                ensure_ascii=False,
                            ),
                        }
                    )
                continue

            if text_content:
                return text_content

            raise ChatRuntimeError("LLM API response did not include text content.")

        raise ChatRuntimeError("LLM API exceeded the maximum number of automatic tool steps.")

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
    ) -> object:
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
            return response.json()
        except ValueError as exc:
            raise ChatRuntimeError("LLM API returned invalid JSON.") from exc

    def _build_payload(
        self,
        model: str,
        messages: list[dict[str, object]],
        *,
        allow_tools: bool,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if allow_tools:
            payload["tools"] = [self._tool_definition()]
        return payload

    @staticmethod
    def _build_initial_messages(
        content: str,
        attachments: list[AttachmentMetadata],
    ) -> list[dict[str, object]]:
        user_content = content.strip()
        if attachments:
            attachment_lines = []
            for attachment in attachments:
                name = attachment.name or "unnamed"
                content_type = attachment.content_type or "unknown"
                size_bytes = (
                    attachment.size_bytes if attachment.size_bytes is not None else "unknown"
                )
                attachment_lines.append(f"- {name} ({content_type}, {size_bytes} bytes)")

            user_content = (
                f"{user_content}\n\n"
                "Attachment metadata provided with this message:\n" + "\n".join(attachment_lines)
            )

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

    @staticmethod
    def _tool_definition() -> dict[str, object]:
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
        return {
            "role": "assistant",
            "content": message.get("content", ""),
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
        cleaned_content = THINK_BLOCK_PATTERN.sub("", content).strip()
        if cleaned_content:
            return cleaned_content

        return "模型已完成分析，但没有返回可展示的最终答复。"

    @staticmethod
    def _extract_tool_calls(message: dict[str, object]) -> list[ToolCallRequest]:
        raw_tool_calls = message.get("tool_calls")
        if not isinstance(raw_tool_calls, list):
            return []

        tool_calls: list[ToolCallRequest] = []
        for raw_tool_call in raw_tool_calls:
            if not isinstance(raw_tool_call, dict):
                continue

            tool_call_id = raw_tool_call.get("id")
            function_payload = raw_tool_call.get("function")
            if not isinstance(tool_call_id, str) or not isinstance(function_payload, dict):
                continue

            function_name = function_payload.get("name")
            if function_name != "execute_kali_command":
                raise ChatRuntimeError("LLM requested an unsupported tool.")

            arguments = function_payload.get("arguments")
            parsed_arguments = OpenAICompatibleChatRuntime._parse_tool_arguments(arguments)
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

            tool_calls.append(
                ToolCallRequest(
                    tool_call_id=tool_call_id,
                    command=command.strip(),
                    timeout_seconds=timeout_seconds,
                    artifact_paths=normalized_artifact_paths,
                )
            )

        return tool_calls

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


def get_chat_runtime() -> ChatRuntime:
    return OpenAICompatibleChatRuntime(get_settings())
