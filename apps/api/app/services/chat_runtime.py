from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.settings import Settings, get_settings
from app.db.models import AttachmentMetadata, SkillAgentSummaryRead

SYSTEM_PROMPT = (
    "You are assisting an authorized defensive security research workflow. "
    "Reply in the user's language. Keep answers concise, evidence-oriented, and within the "
    "user's stated scope. The system exposes a dynamic Skills catalog for the current project. "
    "When the user asks which skills are available, asks what a skill does, or asks you to use "
    "a skill, call list_available_skills or read_skill_content before asking generic "
    "clarifying questions, and do not guess skill contents. Use execute_kali_command only when "
    "shell-based verification or command output would materially improve accuracy. After tool "
    "execution, summarize what happened clearly."
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
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolCallResult:
    tool_name: str
    payload: dict[str, Any] = field(default_factory=dict)


ToolExecutor = Callable[[ToolCallRequest], Awaitable[ToolCallResult]]


class ChatRuntime(Protocol):
    async def generate_reply(
        self,
        content: str,
        attachments: list[AttachmentMetadata],
        available_skills: list[SkillAgentSummaryRead] | None = None,
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
        available_skills: list[SkillAgentSummaryRead] | None = None,
        execute_tool: ToolExecutor | None = None,
    ) -> str:
        api_key, base_url, model = self._require_configuration()
        endpoint = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        messages = self._build_initial_messages(content, attachments, available_skills or [])

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
            payload["tools"] = self._tool_definitions()
        return payload

    @staticmethod
    def _build_initial_messages(
        content: str,
        attachments: list[AttachmentMetadata],
        available_skills: list[SkillAgentSummaryRead],
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

        messages: list[dict[str, object]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        skill_catalog_context = OpenAICompatibleChatRuntime._build_skill_catalog_context(
            available_skills
        )
        if skill_catalog_context is not None:
            messages.append({"role": "system", "content": skill_catalog_context})
        messages.append({"role": "user", "content": user_content})
        return messages

    @staticmethod
    def _tool_definitions() -> list[dict[str, object]]:
        return [
            OpenAICompatibleChatRuntime._execute_kali_command_definition(),
            OpenAICompatibleChatRuntime._list_available_skills_definition(),
            OpenAICompatibleChatRuntime._read_skill_content_definition(),
        ]

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
            "call the skills tools before asking broad clarification questions."
        )
        return "\n".join(lines)

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


def get_chat_runtime() -> ChatRuntime:
    return OpenAICompatibleChatRuntime(get_settings())
