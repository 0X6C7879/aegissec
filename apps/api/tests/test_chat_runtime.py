from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any, cast

import httpx
import pytest
from pytest import MonkeyPatch

from app.core.settings import Settings
from app.db.models import AttachmentMetadata, MessageRole, SkillAgentSummaryRead
from app.harness.messages import GenerationCallbacks as HarnessGenerationCallbacks
from app.harness.messages import ProviderTurnResult
from app.harness.query_engine import BaseQueryEngine
from app.harness.query_loop import QueryLoop
from app.services.chat_runtime import (
    MAX_TOOL_STEPS,
    AnthropicChatRuntime,
    ChatRuntimeError,
    ConversationMessage,
    GenerationCallbacks,
    OpenAICompatibleChatRuntime,
    ToolCallRequest,
    ToolCallResult,
    get_chat_runtime,
)
from app.services.llm_rate_limit import reset_llm_rate_limiter_cache


def test_extract_message_content_preserves_think_blocks_in_string_payload() -> None:
    content = "<think>internal reasoning</think>\n\n最终答复"

    result = OpenAICompatibleChatRuntime._extract_message_content(content)

    assert result == "<think>internal reasoning</think>\n\n最终答复"


def test_extract_message_content_preserves_think_blocks_from_text_parts() -> None:
    content = [
        {"type": "text", "text": "<think>internal reasoning</think>"},
        {"type": "text", "text": "最终答复"},
    ]

    result = OpenAICompatibleChatRuntime._extract_message_content(content)

    assert result == "<think>internal reasoning</think>\n最终答复"


def test_extract_message_content_preserves_tool_protocol_markup_from_string_payload() -> None:
    content = (
        '<minimax:tool_call id="tool-1"><invoke name="agent-browser">'
        '{"task":"demo"}</invoke></minimax:tool_call>\n\n最终答复'
    )

    result = OpenAICompatibleChatRuntime._extract_message_content(content)

    assert result == content


def test_assistant_message_for_history_preserves_tool_protocol_markup() -> None:
    message: dict[str, object] = {
        "content": (
            '<minimax:tool_call id="tool-1"><invoke name="agent-browser">'
            '{"task":"demo"}</invoke></minimax:tool_call>最终答复'
        ),
        "tool_calls": [
            {
                "id": "call-1",
                "function": {
                    "name": "execute_kali_command",
                    "arguments": json.dumps({"command": "pwd"}),
                },
            }
        ],
    }

    history_message = OpenAICompatibleChatRuntime._assistant_message_for_history(message)

    assert history_message == {
        "role": "assistant",
        "content": (
            '<minimax:tool_call id="tool-1"><invoke name="agent-browser">'
            '{"task":"demo"}</invoke></minimax:tool_call>最终答复'
        ),
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "execute_kali_command",
                    "arguments": json.dumps({"command": "pwd"}),
                },
            }
        ],
    }


def test_assistant_message_for_history_uses_null_content_for_tool_only_turn() -> None:
    tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {
                "name": "execute_kali_command",
                "arguments": json.dumps({"command": "pwd"}),
            },
        }
    ]
    message: dict[str, object] = {
        "content": "",
        "tool_calls": tool_calls,
    }

    history_message = OpenAICompatibleChatRuntime._assistant_message_for_history(message)

    assert history_message == {
        "role": "assistant",
        "content": None,
        "tool_calls": tool_calls,
    }


def test_assistant_message_for_history_canonicalizes_tool_calls() -> None:
    message: dict[str, object] = {
        "content": "",
        "tool_calls": [
            {
                "id": "call-1",
                "index": 0,
                "function": {
                    "name": "execute_kali_command",
                    "arguments": {
                        "command": "pwd",
                        "artifact_paths": ["reports/out.txt"],
                    },
                },
            }
        ],
    }

    history_message = OpenAICompatibleChatRuntime._assistant_message_for_history(message)

    assert history_message == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "execute_kali_command",
                    "arguments": json.dumps(
                        {"command": "pwd", "artifact_paths": ["reports/out.txt"]},
                        ensure_ascii=False,
                    ),
                },
            }
        ],
    }


def test_extract_tool_calls_supports_shell_skill_and_terminal_tools() -> None:
    message: dict[str, object] = {
        "tool_calls": [
            {
                "id": "call-1",
                "function": {
                    "name": "execute_kali_command",
                    "arguments": json.dumps(
                        {
                            "command": "pwd",
                            "timeout_seconds": 15,
                            "artifact_paths": ["reports/out.txt"],
                        }
                    ),
                },
            },
            {
                "id": "call-2",
                "function": {
                    "name": "list_available_skills",
                    "arguments": {},
                },
            },
            {
                "id": "call-3",
                "function": {
                    "name": "execute_skill",
                    "arguments": {"skill_name_or_id": "adscan"},
                },
            },
            {
                "id": "call-4",
                "function": {
                    "name": "read_skill_content",
                    "arguments": {"skill_name_or_id": "adscan"},
                },
            },
            {
                "id": "call-5",
                "function": {
                    "name": "create_terminal_session",
                    "arguments": {
                        "title": "Ops",
                        "shell": "/bin/zsh",
                        "cwd": "/workspace",
                        "metadata": {"origin": "chat"},
                    },
                },
            },
            {
                "id": "call-6",
                "function": {
                    "name": "list_terminal_sessions",
                    "arguments": {},
                },
            },
            {
                "id": "call-7",
                "function": {
                    "name": "execute_terminal_command",
                    "arguments": {
                        "terminal_id": "term-a",
                        "command": "pwd",
                        "detach": True,
                        "timeout_seconds": 30,
                        "artifact_paths": ["reports/out.txt"],
                    },
                },
            },
            {
                "id": "call-8",
                "function": {
                    "name": "read_terminal_buffer",
                    "arguments": {
                        "terminal_id": "term-a",
                        "job_id": "job-1",
                        "stream": "stderr",
                        "lines": 25,
                    },
                },
            },
            {
                "id": "call-9",
                "function": {
                    "name": "stop_terminal_job",
                    "arguments": {"job_id": "job-1"},
                },
            },
        ]
    }

    tool_calls = OpenAICompatibleChatRuntime._extract_tool_calls(message)

    assert [(tool_call.tool_call_id, tool_call.tool_name) for tool_call in tool_calls] == [
        ("call-1", "execute_kali_command"),
        ("call-2", "list_available_skills"),
        ("call-3", "execute_skill"),
        ("call-4", "read_skill_content"),
        ("call-5", "create_terminal_session"),
        ("call-6", "list_terminal_sessions"),
        ("call-7", "execute_terminal_command"),
        ("call-8", "read_terminal_buffer"),
        ("call-9", "stop_terminal_job"),
    ]
    assert tool_calls[0].arguments == {
        "command": "pwd",
        "timeout_seconds": 15,
        "artifact_paths": ["reports/out.txt"],
    }
    assert tool_calls[1].arguments == {}
    assert tool_calls[2].arguments == {"skill_name_or_id": "adscan"}
    assert tool_calls[3].arguments == {"skill_name_or_id": "adscan"}
    assert tool_calls[4].arguments == {
        "title": "Ops",
        "shell": "/bin/zsh",
        "cwd": "/workspace",
        "metadata": {"origin": "chat"},
    }
    assert tool_calls[5].arguments == {}
    assert tool_calls[6].arguments == {
        "terminal_id": "term-a",
        "command": "pwd",
        "detach": True,
        "timeout_seconds": 30,
        "artifact_paths": ["reports/out.txt"],
    }
    assert tool_calls[7].arguments == {
        "terminal_id": "term-a",
        "job_id": "job-1",
        "stream": "stderr",
        "lines": 25,
    }
    assert tool_calls[8].arguments == {"job_id": "job-1"}


def test_anthropic_extract_tool_request_supports_terminal_tools() -> None:
    create_request = AnthropicChatRuntime._extract_tool_request_from_use(
        {
            "id": "tool-1",
            "name": "create_terminal_session",
            "input": {
                "title": "Ops",
                "shell": "/bin/zsh",
                "cwd": "/workspace",
                "metadata": {"origin": "chat"},
            },
        }
    )
    execute_request = AnthropicChatRuntime._extract_tool_request_from_use(
        {
            "id": "tool-2",
            "name": "execute_terminal_command",
            "input": {
                "terminal_id": "term-a",
                "command": "pwd",
                "detach": True,
                "timeout_seconds": 30,
                "artifact_paths": ["reports/out.txt"],
            },
        }
    )
    read_request = AnthropicChatRuntime._extract_tool_request_from_use(
        {
            "id": "tool-3",
            "name": "read_terminal_buffer",
            "input": {
                "terminal_id": "term-a",
                "job_id": "job-1",
                "stream": "stdout",
                "lines": 50,
            },
        }
    )
    stop_request = AnthropicChatRuntime._extract_tool_request_from_use(
        {
            "id": "tool-4",
            "name": "stop_terminal_job",
            "input": {"job_id": "job-1"},
        }
    )

    assert create_request.tool_name == "create_terminal_session"
    assert create_request.arguments == {
        "title": "Ops",
        "shell": "/bin/zsh",
        "cwd": "/workspace",
        "metadata": {"origin": "chat"},
    }
    assert execute_request.tool_name == "execute_terminal_command"
    assert execute_request.arguments == {
        "terminal_id": "term-a",
        "command": "pwd",
        "detach": True,
        "timeout_seconds": 30,
        "artifact_paths": ["reports/out.txt"],
    }
    assert read_request.tool_name == "read_terminal_buffer"
    assert read_request.arguments == {
        "terminal_id": "term-a",
        "job_id": "job-1",
        "stream": "stdout",
        "lines": 50,
    }
    assert stop_request.tool_name == "stop_terminal_job"
    assert stop_request.arguments == {"job_id": "job-1"}


def test_extract_tool_calls_coerces_loaded_skill_name_to_execute_skill() -> None:
    message: dict[str, object] = {
        "tool_calls": [
            {
                "id": "call-1",
                "function": {
                    "name": "agent-browser",
                    "arguments": {},
                },
            }
        ]
    }

    tool_calls = OpenAICompatibleChatRuntime._extract_tool_calls(
        message,
        [
            SkillAgentSummaryRead(
                id="skill-1",
                name="agent-browser",
                directory_name="agent-browser",
                description="Browser automation skill.",
                compatibility=[],
                entry_file="skills/agent-browser/SKILL.md",
            )
        ],
    )

    assert len(tool_calls) == 1
    assert tool_calls[0].tool_call_id == "call-1"
    assert tool_calls[0].tool_name == "execute_skill"
    assert tool_calls[0].arguments == {"skill_name_or_id": "agent-browser"}


def test_openai_tool_definitions_include_mcp_tools_and_safe_schema_fallback() -> None:
    definitions = OpenAICompatibleChatRuntime._tool_definitions(
        [
            {
                "tool_alias": "mcp__burp_suite__scan_target",
                "server_id": "server-1",
                "server_name": "Burp Suite",
                "source": "local",
                "scope": "project",
                "transport": "stdio",
                "tool_name": "scan-target",
                "tool_title": "Scan Target",
                "tool_description": "Run a focused MCP scan.",
                "input_schema": None,
            }
        ]
    )

    openai_functions = [cast(dict[str, object], item["function"]) for item in definitions]
    function_names = [str(item["name"]) for item in openai_functions]
    execute_kali_parameters = cast(dict[str, object], openai_functions[0]["parameters"])
    execute_kali_properties = cast(dict[str, object], execute_kali_parameters["properties"])
    timeout_schema = cast(dict[str, object], execute_kali_properties["timeout_seconds"])

    assert function_names[:9] == [
        "execute_kali_command",
        "list_available_skills",
        "execute_skill",
        "read_skill_content",
        "create_terminal_session",
        "list_terminal_sessions",
        "execute_terminal_command",
        "read_terminal_buffer",
        "stop_terminal_job",
    ]
    assert timeout_schema["type"] == "integer"
    assert function_names[9] == "mcp__burp_suite__scan_target"
    assert openai_functions[9]["parameters"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }


def test_anthropic_tool_definitions_include_mcp_tools_and_safe_schema_fallback() -> None:
    definitions = AnthropicChatRuntime._tool_definitions(
        [
            {
                "tool_alias": "mcp__burp_suite__scan_target",
                "server_id": "server-1",
                "server_name": "Burp Suite",
                "source": "local",
                "scope": "project",
                "transport": "stdio",
                "tool_name": "scan-target",
                "tool_title": "Scan Target",
                "tool_description": "Run a focused MCP scan.",
                "input_schema": {"type": "array"},
            }
        ]
    )

    assert [item["name"] for item in definitions[:9]] == [
        "execute_kali_command",
        "list_available_skills",
        "execute_skill",
        "read_skill_content",
        "create_terminal_session",
        "list_terminal_sessions",
        "execute_terminal_command",
        "read_terminal_buffer",
        "stop_terminal_job",
    ]
    assert definitions[9]["name"] == "mcp__burp_suite__scan_target"
    assert definitions[9]["input_schema"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }


def test_extract_tool_calls_resolves_mcp_alias_to_server_and_tool() -> None:
    message: dict[str, object] = {
        "tool_calls": [
            {
                "id": "call-mcp-1",
                "function": {
                    "name": "mcp__burp_suite__scan_target",
                    "arguments": json.dumps({"target": "https://example.test"}),
                },
            },
            {
                "id": "call-fixed-1",
                "function": {"name": "list_available_skills", "arguments": {}},
            },
        ]
    }

    tool_calls = OpenAICompatibleChatRuntime._extract_tool_calls(
        message,
        mcp_tools=[
            {
                "tool_alias": "mcp__burp_suite__scan_target",
                "server_id": "server-1",
                "server_name": "Burp Suite",
                "source": "local",
                "scope": "project",
                "transport": "stdio",
                "tool_name": "scan-target",
                "tool_title": "Scan Target",
                "tool_description": "Run a focused MCP scan.",
                "input_schema": {"type": "object", "properties": {"target": {"type": "string"}}},
            }
        ],
    )

    assert tool_calls[0].tool_name == "mcp__burp_suite__scan_target"
    assert tool_calls[0].mcp_server_id == "server-1"
    assert tool_calls[0].mcp_tool_name == "scan-target"
    assert tool_calls[0].arguments == {"target": "https://example.test"}
    assert tool_calls[1].tool_name == "list_available_skills"


def test_anthropic_extract_tool_request_from_use_resolves_mcp_alias() -> None:
    request = AnthropicChatRuntime._extract_tool_request_from_use(
        {
            "type": "tool_use",
            "id": "tool-use-mcp-1",
            "name": "mcp__burp_suite__scan_target",
            "input": {"target": "https://example.test"},
        },
        mcp_tools=[
            {
                "tool_alias": "mcp__burp_suite__scan_target",
                "server_id": "server-1",
                "server_name": "Burp Suite",
                "source": "local",
                "scope": "project",
                "transport": "stdio",
                "tool_name": "scan-target",
                "tool_title": "Scan Target",
                "tool_description": "Run a focused MCP scan.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
    )

    assert request.tool_call_id == "tool-use-mcp-1"
    assert request.tool_name == "mcp__burp_suite__scan_target"
    assert request.mcp_server_id == "server-1"
    assert request.mcp_tool_name == "scan-target"
    assert request.arguments == {"target": "https://example.test"}


def test_openai_build_initial_messages_uses_persisted_conversation_messages() -> None:
    messages = OpenAICompatibleChatRuntime._build_initial_messages(
        "ignored latest prompt",
        [],
        [],
        skill_context_prompt=None,
        conversation_messages=[
            ConversationMessage(
                role=MessageRole.USER,
                content="first question",
                attachments=[
                    AttachmentMetadata(name="scope.txt", content_type="text/plain", size_bytes=12)
                ],
            ),
            ConversationMessage(role=MessageRole.ASSISTANT, content="first answer"),
            ConversationMessage(role=MessageRole.USER, content="follow-up"),
        ],
    )

    assert messages[0]["role"] == "system"
    assert [message["role"] for message in messages[1:]] == ["user", "assistant", "user"]
    assert "first question" in str(messages[1]["content"])
    assert "scope.txt" in str(messages[1]["content"])
    assert messages[-1]["content"] == "follow-up"


def test_anthropic_build_initial_messages_prefixes_capability_prompt_on_first_message() -> None:
    messages = AnthropicChatRuntime._build_initial_messages(
        "latest prompt",
        [],
        [
            SkillAgentSummaryRead(
                id="skill-1",
                name="agent-browser",
                directory_name="agent-browser",
                description="Browser automation skill.",
                compatibility=[],
                entry_file="skills/agent-browser/SKILL.md",
            )
        ],
        skill_context_prompt="Loaded skills context.",
        conversation_messages=[ConversationMessage(role=MessageRole.USER, content="follow-up")],
    )

    assert messages[0]["role"] == "user"
    assert "Loaded skills context." in str(messages[0]["content"])
    assert "follow-up" in str(messages[0]["content"])


def _build_openai_tool_response(call_id: int, command: str) -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"call-{call_id}",
                            "function": {
                                "name": "execute_kali_command",
                                "arguments": json.dumps({"command": command}),
                            },
                        }
                    ],
                }
            }
        ]
    }


def _build_anthropic_tool_response(call_id: int, command: str) -> dict[str, object]:
    return {
        "content": [
            {
                "type": "tool_use",
                "id": f"call-{call_id}",
                "name": "execute_kali_command",
                "input": {"command": command},
            }
        ]
    }


class _FakeAsyncClient:
    def __init__(
        self,
        responses: list[httpx.Response],
        *,
        stream_lines: list[list[str]] | None = None,
    ) -> None:
        self._responses = responses
        self._stream_lines = stream_lines or []

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb

    async def post(
        self,
        endpoint: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
    ) -> httpx.Response:
        del endpoint, headers, json
        return self._responses.pop(0)

    def stream(
        self,
        method: str,
        endpoint: str,
        *,
        headers: dict[str, str],
        json: dict[str, object],
    ) -> _FakeStreamContext:
        del method, endpoint, headers, json
        return _FakeStreamContext(
            response=self._responses.pop(0),
            lines=self._stream_lines.pop(0) if self._stream_lines else [],
        )


class _FakeStreamContext:
    def __init__(self, *, response: httpx.Response, lines: list[str]) -> None:
        self._response = response
        self._lines = lines

    async def __aenter__(self) -> _FakeStreamResponse:
        return _FakeStreamResponse(response=self._response, lines=self._lines)

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb


class _FakeStreamResponse:
    def __init__(self, *, response: httpx.Response, lines: list[str]) -> None:
        self._response = response
        self._lines = lines

    def raise_for_status(self) -> None:
        self._response.raise_for_status()

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


def _httpx_response(
    status_code: int,
    *,
    endpoint: str = "https://example.test/v1/chat/completions",
    headers: dict[str, str] | None = None,
    json_payload: dict[str, object] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        request=httpx.Request("POST", endpoint),
        headers=headers,
        json=json_payload or {},
    )


# Anthropic-specific tests
def test_anthropic_extract_response_content_handles_text_blocks() -> None:
    response_payload = {
        "content": [
            {"type": "text", "text": "Hello, this is a response"},
        ]
    }

    text_content, tool_uses = AnthropicChatRuntime._extract_response_content(response_payload)

    assert text_content == "Hello, this is a response"
    assert tool_uses == []


def test_anthropic_extract_response_content_handles_tool_use_blocks() -> None:
    response_payload = {
        "content": [
            {
                "type": "tool_use",
                "id": "tool-use-1",
                "name": "execute_kali_command",
                "input": {
                    "command": "whoami",
                    "timeout_seconds": 30,
                    "artifact_paths": [],
                },
            },
            {
                "type": "text",
                "text": "I will execute that command",
            },
        ]
    }

    text_content, tool_uses = AnthropicChatRuntime._extract_response_content(response_payload)

    assert text_content == "I will execute that command"
    assert len(tool_uses) == 1
    assert tool_uses[0]["type"] == "tool_use"
    assert tool_uses[0]["id"] == "tool-use-1"
    assert tool_uses[0]["name"] == "execute_kali_command"
    tool_input = tool_uses[0]["input"]
    assert isinstance(tool_input, dict)
    assert tool_input["command"] == "whoami"


def test_query_loop_consumes_pending_context_injections_before_follow_up_turn() -> None:
    class InjectionAwareEngine(BaseQueryEngine):
        def __init__(self) -> None:
            super().__init__(
                messages=[],
                model_name="demo-model",
                system_prompt=None,
                max_turns=4,
            )
            self.turns = 0

        async def request_turn(
            self,
            *,
            allow_tools: bool,
            callbacks: object | None,
        ) -> ProviderTurnResult:
            del allow_tools, callbacks
            self.turns += 1
            if self.turns == 1:
                return ProviderTurnResult(
                    assistant_payload={"content": "first answer"},
                    text_content="first answer",
                )
            return ProviderTurnResult(
                assistant_payload={"content": "second answer"},
                text_content="second answer",
            )

        def append_tool_results(self, **_: object) -> None:
            raise AssertionError("tool execution should not run in this test")

        async def generate_tool_budget_reply(
            self,
            *,
            callbacks: object | None,
        ) -> str:
            del callbacks
            raise AssertionError("tool budget fallback should not be used in this test")

        def render_compact_message(self, compact_fragment: str) -> dict[str, object]:
            return {"role": "user", "content": compact_fragment}

        def append_assistant_response_to_history(
            self, assistant_payload: dict[str, object]
        ) -> None:
            self.messages.append(
                {
                    "role": "assistant",
                    "content": assistant_payload["content"],
                }
            )

        def build_synthetic_assistant_payload(
            self,
            tool_calls: Sequence[object],
        ) -> dict[str, object]:
            del tool_calls
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [],
            }

    engine = InjectionAwareEngine()
    pending_batches = [[], ["extra scope", "focus on host B"]]
    applied_batches: list[list[str]] = []

    async def consume_context_injections() -> list[str]:
        if pending_batches:
            return pending_batches.pop(0)
        return []

    async def on_context_injection_applied(injections: list[str]) -> None:
        applied_batches.append(list(injections))

    result = asyncio.run(
        QueryLoop(max_turns=4).run(
            engine,
            execute_tool=None,
            callbacks=cast(
                HarnessGenerationCallbacks,
                GenerationCallbacks(
                    consume_context_injections=consume_context_injections,
                    on_context_injection_applied=on_context_injection_applied,
                ),
            ),
        )
    )

    assert result == "second answer"
    assert engine.turns == 2
    assert applied_batches == [["extra scope", "focus on host B"]]
    assert engine.messages == [
        {"role": "assistant", "content": "first answer"},
        {
            "role": "user",
            "content": "Additional operator context injected during the active run:\nextra scope",
        },
        {
            "role": "user",
            "content": (
                "Additional operator context injected during the active run:\nfocus on host B"
            ),
        },
    ]


def test_anthropic_extract_response_content_multiple_text_blocks() -> None:
    response_payload = {
        "content": [
            {"type": "text", "text": "First part."},
            {"type": "text", "text": "Second part."},
        ]
    }

    text_content, tool_uses = AnthropicChatRuntime._extract_response_content(response_payload)

    assert text_content == "First part.\nSecond part."
    assert tool_uses == []


def test_anthropic_extract_tool_request_from_use() -> None:
    tool_use: dict[str, object] = {
        "type": "tool_use",
        "id": "call-123",
        "name": "list_available_skills",
        "input": {},
    }

    request = AnthropicChatRuntime._extract_tool_request_from_use(tool_use)

    assert request.tool_call_id == "call-123"
    assert request.tool_name == "list_available_skills"
    assert request.arguments == {}


def test_anthropic_extract_tool_request_from_use_coerces_loaded_skill_name() -> None:
    tool_use: dict[str, object] = {
        "type": "tool_use",
        "id": "call-456",
        "name": "agent-browser",
        "input": {},
    }

    request = AnthropicChatRuntime._extract_tool_request_from_use(
        tool_use,
        [
            SkillAgentSummaryRead(
                id="skill-1",
                name="agent-browser",
                directory_name="agent-browser",
                description="Browser automation skill.",
                compatibility=[],
                entry_file="skills/agent-browser/SKILL.md",
            )
        ],
    )

    assert request.tool_call_id == "call-456"
    assert request.tool_name == "execute_skill"
    assert request.arguments == {"skill_name_or_id": "agent-browser"}


def test_anthropic_build_messages_endpoint_uses_default_base_url() -> None:
    endpoint = AnthropicChatRuntime._build_messages_endpoint("https://api.anthropic.com")

    assert endpoint == "https://api.anthropic.com/v1/messages"


def test_anthropic_build_messages_endpoint_accepts_full_messages_url() -> None:
    endpoint = AnthropicChatRuntime._build_messages_endpoint(
        "https://anthropic-proxy.example.test/v1/messages"
    )

    assert endpoint == "https://anthropic-proxy.example.test/v1/messages"


def test_anthropic_build_messages_endpoint_adds_v1_messages_for_compat_gateway_base() -> None:
    endpoint = AnthropicChatRuntime._build_messages_endpoint("https://api.minimaxi.com/anthropic")

    assert endpoint == "https://api.minimaxi.com/anthropic/v1/messages"


def test_anthropic_build_messages_endpoint_normalizes_legacy_messages_path() -> None:
    endpoint = AnthropicChatRuntime._build_messages_endpoint(
        "https://api.minimaxi.com/anthropic/messages"
    )

    assert endpoint == "https://api.minimaxi.com/anthropic/v1/messages"


def test_openai_runtime_supports_more_than_three_tool_rounds() -> None:
    settings = Settings.model_construct(
        llm_api_key="test-key",
        llm_api_base_url="https://example.test",
        llm_default_model="demo-model",
    )

    class StubRuntime(OpenAICompatibleChatRuntime):
        def __init__(self) -> None:
            super().__init__(settings=settings)
            self._responses: list[dict[str, object]] = [
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "function": {
                                            "name": "execute_kali_command",
                                            "arguments": json.dumps({"command": "pwd"}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call-2",
                                        "function": {
                                            "name": "execute_kali_command",
                                            "arguments": json.dumps(
                                                {"command": "curl -I https://example.test"}
                                            ),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call-3",
                                        "function": {
                                            "name": "execute_kali_command",
                                            "arguments": json.dumps({"command": "ls -la"}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call-4",
                                        "function": {
                                            "name": "execute_kali_command",
                                            "arguments": json.dumps({"command": "cat report.txt"}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Final answer",
                            }
                        }
                    ]
                },
            ]

        async def _request_completion(
            self,
            endpoint: str,
            headers: dict[str, str],
            payload: dict[str, object],
        ) -> dict[str, object]:
            del endpoint, headers, payload
            return self._responses.pop(0)

    runtime = StubRuntime()
    executed_commands: list[str] = []

    async def execute_tool(tool_request: ToolCallRequest) -> ToolCallResult:
        executed_commands.append(str(tool_request.arguments["command"]))
        return ToolCallResult(
            tool_name=tool_request.tool_name,
            payload={"status": "completed", "stdout": "ok", "stderr": "", "artifacts": []},
        )

    result = asyncio.run(
        runtime.generate_reply("Collect initial evidence", [], execute_tool=execute_tool)
    )

    assert result == "Final answer"
    assert executed_commands == [
        "pwd",
        "curl -I https://example.test",
        "ls -la",
        "cat report.txt",
    ]


def test_anthropic_runtime_supports_more_than_three_tool_rounds() -> None:
    settings = Settings.model_construct(
        anthropic_api_key="test-key",
        anthropic_api_base_url="https://example.test",
        anthropic_model="claude-demo",
    )

    class StubRuntime(AnthropicChatRuntime):
        def __init__(self) -> None:
            super().__init__(settings=settings)
            self._responses: list[dict[str, object]] = [
                {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "execute_kali_command",
                            "input": {"command": "pwd"},
                        }
                    ]
                },
                {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-2",
                            "name": "execute_kali_command",
                            "input": {"command": "curl -I https://example.test"},
                        }
                    ]
                },
                {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-3",
                            "name": "execute_kali_command",
                            "input": {"command": "ls -la"},
                        }
                    ]
                },
                {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-4",
                            "name": "execute_kali_command",
                            "input": {"command": "cat report.txt"},
                        }
                    ]
                },
                {"content": [{"type": "text", "text": "Final answer"}]},
            ]

        async def _request_completion(
            self,
            endpoint: str,
            headers: dict[str, str],
            payload: dict[str, object],
        ) -> dict[str, object]:
            del endpoint, headers, payload
            return self._responses.pop(0)

    runtime = StubRuntime()
    executed_commands: list[str] = []

    async def execute_tool(tool_request: ToolCallRequest) -> ToolCallResult:
        executed_commands.append(str(tool_request.arguments["command"]))
        return ToolCallResult(
            tool_name=tool_request.tool_name,
            payload={"status": "completed", "stdout": "ok", "stderr": "", "artifacts": []},
        )

    result = asyncio.run(
        runtime.generate_reply("Collect initial evidence", [], execute_tool=execute_tool)
    )

    assert result == "Final answer"
    assert executed_commands == [
        "pwd",
        "curl -I https://example.test",
        "ls -la",
        "cat report.txt",
    ]


def test_openai_runtime_returns_summary_when_tool_budget_is_exhausted() -> None:
    settings = Settings.model_construct(
        llm_api_key="test-key",
        llm_api_base_url="https://example.test",
        llm_default_model="demo-model",
    )

    class StubRuntime(OpenAICompatibleChatRuntime):
        def __init__(self) -> None:
            super().__init__(settings=settings)
            self.payloads: list[dict[str, object]] = []
            self._responses: list[dict[str, object]] = [
                _build_openai_tool_response(index + 1, f"cmd-{index + 1}")
                for index in range(MAX_TOOL_STEPS + 1)
            ]

        async def _request_completion(
            self,
            endpoint: str,
            headers: dict[str, str],
            payload: dict[str, object],
        ) -> dict[str, object]:
            del endpoint, headers
            self.payloads.append(payload)
            if payload.get("tools") is None:
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "Budget summary",
                            }
                        }
                    ]
                }
            return self._responses.pop(0)

    runtime = StubRuntime()
    executed_commands: list[str] = []

    async def execute_tool(tool_request: ToolCallRequest) -> ToolCallResult:
        executed_commands.append(str(tool_request.arguments["command"]))
        return ToolCallResult(
            tool_name=tool_request.tool_name,
            payload={"status": "completed", "stdout": "ok", "stderr": "", "artifacts": []},
        )

    result = asyncio.run(
        runtime.generate_reply("Collect initial evidence", [], execute_tool=execute_tool)
    )

    assert result == "Budget summary"
    assert executed_commands == [f"cmd-{index + 1}" for index in range(MAX_TOOL_STEPS + 1)]
    assert "tools" not in runtime.payloads[-1]


def test_anthropic_runtime_returns_summary_when_tool_budget_is_exhausted() -> None:
    settings = Settings.model_construct(
        anthropic_api_key="test-key",
        anthropic_api_base_url="https://example.test",
        anthropic_model="claude-demo",
    )

    class StubRuntime(AnthropicChatRuntime):
        def __init__(self) -> None:
            super().__init__(settings=settings)
            self.payloads: list[dict[str, object]] = []
            self._responses: list[dict[str, object]] = [
                _build_anthropic_tool_response(index + 1, f"cmd-{index + 1}")
                for index in range(MAX_TOOL_STEPS + 1)
            ]

        async def _request_completion(
            self,
            endpoint: str,
            headers: dict[str, str],
            payload: dict[str, object],
        ) -> dict[str, object]:
            del endpoint, headers
            self.payloads.append(payload)
            if payload.get("tools") is None:
                return {"content": [{"type": "text", "text": "Budget summary"}]}
            return self._responses.pop(0)

    runtime = StubRuntime()
    executed_commands: list[str] = []

    async def execute_tool(tool_request: ToolCallRequest) -> ToolCallResult:
        executed_commands.append(str(tool_request.arguments["command"]))
        return ToolCallResult(
            tool_name=tool_request.tool_name,
            payload={"status": "completed", "stdout": "ok", "stderr": "", "artifacts": []},
        )

    result = asyncio.run(
        runtime.generate_reply("Collect initial evidence", [], execute_tool=execute_tool)
    )

    assert result == "Budget summary"
    assert executed_commands == [f"cmd-{index + 1}" for index in range(MAX_TOOL_STEPS + 1)]
    assert "tools" not in runtime.payloads[-1]


def test_get_chat_runtime_applies_configured_timeout_for_openai(monkeypatch: MonkeyPatch) -> None:
    settings = Settings.model_construct(
        llm_provider="openai",
        llm_api_key="test-key",
        llm_api_base_url="https://example.test",
        llm_default_model="demo-model",
        llm_request_timeout_seconds=75,
    )

    monkeypatch.setattr("app.services.chat_runtime.get_settings", lambda: settings)

    runtime = get_chat_runtime()

    assert isinstance(runtime, OpenAICompatibleChatRuntime)
    assert runtime._timeout_seconds == 75  # noqa: SLF001


def test_get_chat_runtime_applies_configured_timeout_for_anthropic(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = Settings.model_construct(
        llm_provider="anthropic",
        anthropic_api_key="test-key",
        anthropic_model="claude-demo",
        llm_request_timeout_seconds=90,
    )

    monkeypatch.setattr("app.services.chat_runtime.get_settings", lambda: settings)

    runtime = get_chat_runtime()

    assert isinstance(runtime, AnthropicChatRuntime)
    assert runtime._timeout_seconds == 90  # noqa: SLF001


def test_openai_request_completion_retries_rate_limit_with_retry_after(
    monkeypatch: MonkeyPatch,
) -> None:
    reset_llm_rate_limiter_cache()
    settings = Settings.model_construct(
        llm_api_key="test-key",
        llm_api_base_url="https://example.test",
        llm_default_model="demo-model",
        llm_rate_limit_max_retries=1,
        llm_rate_limit_base_delay_ms=500,
        llm_rate_limit_max_delay_seconds=30,
    )
    runtime = OpenAICompatibleChatRuntime(settings=settings)
    sleep_calls: list[float] = []
    finalized: list[bool] = []
    noted_backoffs: list[float] = []

    class FakeRateController:
        def __init__(self, config: object) -> None:
            self.config = config

        async def acquire(self, payload: dict[str, object], *, max_output_tokens: int) -> object:
            del payload, max_output_tokens
            return object()

        async def finalize(
            self,
            lease: object,
            *,
            rate_limited: bool,
            actual_input_tokens: int | None = None,
            actual_output_tokens: int | None = None,
            actual_total_tokens: int | None = None,
        ) -> None:
            del lease, actual_input_tokens, actual_output_tokens, actual_total_tokens
            finalized.append(rate_limited)

        async def note_backoff(self, delay_seconds: float) -> None:
            noted_backoffs.append(delay_seconds)

    runtime._rate_controller = cast(  # noqa: SLF001
        Any,
        FakeRateController(runtime._rate_controller.config),
    )

    responses = [
        httpx.Response(
            429,
            headers={"Retry-After": "2"},
            request=httpx.Request("POST", "https://example.test"),
            json={"error": {"message": "too many requests"}},
        ),
        httpx.Response(
            200,
            request=httpx.Request("POST", "https://example.test"),
            json={
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
            },
        ),
    ]

    class FakeAsyncClient:
        def __init__(self, *, timeout: object) -> None:
            del timeout

        async def __aenter__(self) -> FakeAsyncClient:
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
            del exc_type, exc, tb

        async def post(
            self,
            endpoint: str,
            *,
            headers: dict[str, str],
            json: dict[str, object],
        ) -> httpx.Response:
            del endpoint, headers, json
            return responses.pop(0)

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr("app.services.chat_runtime.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.services.chat_runtime.asyncio.sleep", fake_sleep)

    result = asyncio.run(
        runtime._request_completion(  # noqa: SLF001
            "https://example.test/v1/chat/completions",
            {"Authorization": "Bearer test"},
            {"model": "demo-model", "messages": [], "max_tokens": 256},
        )
    )

    choices = cast(list[dict[str, object]], result["choices"])
    message = cast(dict[str, object], choices[0]["message"])
    assert message["content"] == "ok"
    assert sleep_calls == [2.0]
    assert noted_backoffs == [2.0]
    assert finalized == [True, False]


def test_openai_build_payload_uses_configured_max_output_tokens() -> None:
    settings = Settings.model_construct(
        llm_api_key="test-key",
        llm_api_base_url="https://example.test",
        llm_default_model="demo-model",
        llm_max_output_tokens=1536,
    )

    payload = OpenAICompatibleChatRuntime(settings=settings)._build_payload(  # noqa: SLF001
        "demo-model",
        [{"role": "user", "content": "hello"}],
        mcp_tools=None,
        allow_tools=False,
        stream=False,
    )

    assert payload["max_tokens"] == 1536


def test_openai_request_completion_retries_on_429(monkeypatch: MonkeyPatch) -> None:
    reset_llm_rate_limiter_cache()
    settings = Settings.model_construct(
        llm_api_key="test-key",
        llm_api_base_url="https://example.test",
        llm_default_model="demo-model",
        llm_max_output_tokens=128,
        llm_max_concurrency=1,
        llm_rate_limit_rpm=10_000,
        llm_rate_limit_tpm_total=10_000,
        llm_rate_limit_tpm_input=10_000,
        llm_rate_limit_tpm_output=10_000,
        llm_rate_limit_safety_ratio=1.0,
        llm_rate_limit_max_retries=2,
        llm_rate_limit_base_delay_ms=10,
        llm_rate_limit_max_delay_seconds=1,
    )
    runtime = OpenAICompatibleChatRuntime(settings=settings)
    sleeps: list[float] = []
    responses = [
        _httpx_response(
            429,
            headers={"retry-after-ms": "50"},
            json_payload={"error": {"message": "rate_limited"}},
        ),
        _httpx_response(
            200,
            json_payload={
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            },
        ),
    ]

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(
        "app.services.chat_runtime.httpx.AsyncClient",
        lambda timeout: _FakeAsyncClient(responses),
    )
    monkeypatch.setattr("app.services.chat_runtime.asyncio.sleep", fake_sleep)

    result = asyncio.run(
        runtime._request_completion(
            "https://example.test/v1/chat/completions",
            {"authorization": "Bearer test-key"},
            {"model": "demo-model", "messages": [], "max_tokens": 128, "stream": False},
        )
    )

    choices = cast(list[dict[str, object]], result["choices"])
    message = cast(dict[str, object], choices[0]["message"])
    assert message["content"] == "ok"
    assert sleeps
    assert sleeps[0] >= 0.05


def test_openai_request_completion_includes_response_body_on_400(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = Settings.model_construct(
        llm_api_key="test-key",
        llm_api_base_url="https://example.test",
        llm_default_model="demo-model",
        llm_max_output_tokens=128,
    )
    runtime = OpenAICompatibleChatRuntime(settings=settings)
    responses = [
        httpx.Response(
            400,
            request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
            text='{"error":{"message":"tool message must include name"}}',
        )
    ]

    monkeypatch.setattr(
        "app.services.chat_runtime.httpx.AsyncClient",
        lambda timeout: _FakeAsyncClient(responses),
    )

    with pytest.raises(ChatRuntimeError) as excinfo:
        asyncio.run(
            runtime._request_completion(
                "https://example.test/v1/chat/completions",
                {"authorization": "Bearer test-key"},
                {"model": "demo-model", "messages": [], "max_tokens": 128, "stream": False},
            )
        )

    assert "status 400" in str(excinfo.value)
    assert "tool message must include name" in str(excinfo.value)


def test_openai_response_body_excerpt_returns_none_when_stream_is_closed() -> None:
    class _ClosedStreamResponse:
        @property
        def text(self) -> str:
            raise httpx.ResponseNotRead()

        async def aread(self) -> bytes:
            raise httpx.StreamClosed()

    excerpt = asyncio.run(
        OpenAICompatibleChatRuntime._response_body_excerpt(
            cast(httpx.Response, _ClosedStreamResponse())
        )
    )

    assert excerpt is None


def test_anthropic_stream_completion_retries_on_429(monkeypatch: MonkeyPatch) -> None:
    reset_llm_rate_limiter_cache()
    settings = Settings.model_construct(
        anthropic_api_key="test-key",
        anthropic_api_base_url="https://example.test",
        anthropic_model="claude-demo",
        llm_max_output_tokens=128,
        llm_max_concurrency=1,
        llm_rate_limit_rpm=10_000,
        llm_rate_limit_tpm_total=10_000,
        llm_rate_limit_tpm_input=10_000,
        llm_rate_limit_tpm_output=10_000,
        llm_rate_limit_safety_ratio=1.0,
        llm_rate_limit_max_retries=2,
        llm_rate_limit_base_delay_ms=10,
        llm_rate_limit_max_delay_seconds=1,
    )
    runtime = AnthropicChatRuntime(settings=settings)
    sleeps: list[float] = []
    responses = [
        _httpx_response(
            429,
            endpoint="https://example.test/v1/messages",
            headers={"retry-after": "0.05"},
            json_payload={"error": {"message": "rate_limited"}},
        ),
        _httpx_response(200, endpoint="https://example.test/v1/messages"),
    ]
    stream_lines = [
        [],
        [
            "event: content_block_delta",
            'data: {"delta":{"type":"text_delta","text":"stream ok"}}',
            "data: [DONE]",
        ],
    ]

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(
        "app.services.chat_runtime.httpx.AsyncClient",
        lambda timeout: _FakeAsyncClient(responses, stream_lines=stream_lines),
    )
    monkeypatch.setattr("app.services.chat_runtime.asyncio.sleep", fake_sleep)

    result = asyncio.run(
        runtime._stream_completion(
            "https://example.test/v1/messages",
            {"x-api-key": "test-key"},
            {
                "model": "claude-demo",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 128,
                "stream": True,
            },
            GenerationCallbacks(),
        )
    )

    assert result == {"content": [{"type": "text", "text": "stream ok"}]}
    assert sleeps
    assert sleeps[0] >= 0.05


def test_openai_stream_completion_preserves_tool_call_type(monkeypatch: MonkeyPatch) -> None:
    reset_llm_rate_limiter_cache()
    settings = Settings.model_construct(
        llm_api_key="test-key",
        llm_api_base_url="https://example.test",
        llm_default_model="demo-model",
        llm_max_output_tokens=128,
    )
    runtime = OpenAICompatibleChatRuntime(settings=settings)
    responses = [_httpx_response(200)]
    first_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": "execute_kali_command",
                                "arguments": '{"command":',
                            },
                        }
                    ]
                }
            }
        ]
    }
    second_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "function": {
                                "arguments": ' "pwd"}',
                            },
                        }
                    ]
                }
            }
        ]
    }
    stream_lines = [
        [
            f"data: {json.dumps(first_chunk)}",
            f"data: {json.dumps(second_chunk)}",
            "data: [DONE]",
        ]
    ]

    monkeypatch.setattr(
        "app.services.chat_runtime.httpx.AsyncClient",
        lambda timeout: _FakeAsyncClient(responses, stream_lines=stream_lines),
    )

    result = asyncio.run(
        runtime._stream_completion(
            "https://example.test/v1/chat/completions",
            {"authorization": "Bearer test-key"},
            {"model": "demo-model", "messages": [], "max_tokens": 128, "stream": True},
            GenerationCallbacks(),
        )
    )

    choices = cast(list[dict[str, object]], result["choices"])
    message = cast(dict[str, object], choices[0]["message"])
    tool_calls = cast(list[dict[str, object]], message["tool_calls"])
    function_payload = cast(dict[str, object], tool_calls[0]["function"])

    assert message["content"] == ""
    assert tool_calls[0]["id"] == "call-1"
    assert tool_calls[0]["type"] == "function"
    assert function_payload["name"] == "execute_kali_command"
    assert json.loads(cast(str, function_payload["arguments"])) == {"command": "pwd"}


def test_openai_stream_completion_includes_response_body_on_400(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = Settings.model_construct(
        llm_api_key="test-key",
        llm_api_base_url="https://example.test",
        llm_default_model="demo-model",
        llm_max_output_tokens=128,
    )
    runtime = OpenAICompatibleChatRuntime(settings=settings)
    responses = [
        httpx.Response(
            400,
            request=httpx.Request("POST", "https://example.test/v1/chat/completions"),
            text='{"error":{"message":"assistant.tool_calls.0.function.arguments must be string"}}',
        )
    ]

    monkeypatch.setattr(
        "app.services.chat_runtime.httpx.AsyncClient",
        lambda timeout: _FakeAsyncClient(responses, stream_lines=[[]]),
    )

    with pytest.raises(ChatRuntimeError) as excinfo:
        asyncio.run(
            runtime._stream_completion(
                "https://example.test/v1/chat/completions",
                {"authorization": "Bearer test-key"},
                {"model": "demo-model", "messages": [], "max_tokens": 128, "stream": True},
                GenerationCallbacks(),
            )
        )

    assert "status 400" in str(excinfo.value)
    assert "arguments must be string" in str(excinfo.value)


def test_openai_runtime_streams_when_tools_are_enabled() -> None:
    settings = Settings.model_construct(
        llm_api_key="test-key",
        llm_api_base_url="https://example.test",
        llm_default_model="demo-model",
    )

    class StreamingToolRuntime(OpenAICompatibleChatRuntime):
        def __init__(self) -> None:
            super().__init__(settings=settings)
            self.stream_payloads: list[dict[str, object]] = []
            self.stream_calls = 0

        async def _stream_completion(
            self,
            endpoint: str,
            headers: dict[str, str],
            payload: dict[str, object],
            callbacks: object,
        ) -> dict[str, object]:
            del endpoint, headers
            self.stream_calls += 1
            self.stream_payloads.append(payload)
            if self.stream_calls == 1:
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "execute_kali_command",
                                            "arguments": json.dumps({"command": "pwd"}),
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }
            assert isinstance(callbacks, object)
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "streamed final answer",
                        }
                    }
                ]
            }

        async def _request_completion(
            self,
            endpoint: str,
            headers: dict[str, str],
            payload: dict[str, object],
        ) -> dict[str, object]:
            del endpoint, headers, payload
            raise AssertionError(
                "_request_completion should not be used when callbacks are provided"
            )

    runtime = StreamingToolRuntime()

    async def execute_tool(tool_request: ToolCallRequest) -> ToolCallResult:
        return ToolCallResult(tool_name=tool_request.tool_name, payload={"status": "ok"})

    result = asyncio.run(
        runtime.generate_reply(
            "Collect initial evidence",
            [],
            execute_tool=execute_tool,
            callbacks=GenerationCallbacks(),
        )
    )

    assert result == "streamed final answer"
    assert runtime.stream_calls == 2
    assert all(payload["stream"] is True for payload in runtime.stream_payloads)
    second_messages = cast(list[dict[str, object]], runtime.stream_payloads[1]["messages"])
    assistant_history_message = next(
        message
        for message in second_messages
        if message.get("role") == "assistant" and message.get("tool_calls")
    )
    assert assistant_history_message == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "execute_kali_command",
                    "arguments": json.dumps({"command": "pwd"}),
                },
            }
        ],
    }


def test_openai_runtime_second_round_payload_uses_canonical_tool_history() -> None:
    settings = Settings.model_construct(
        llm_api_key="test-key",
        llm_api_base_url="https://example.test",
        llm_default_model="demo-model",
    )

    class StubRuntime(OpenAICompatibleChatRuntime):
        def __init__(self) -> None:
            super().__init__(settings=settings)
            self.payloads: list[dict[str, object]] = []

        async def _request_completion(
            self,
            endpoint: str,
            headers: dict[str, str],
            payload: dict[str, object],
        ) -> dict[str, object]:
            del endpoint, headers
            self.payloads.append(payload)
            if len(self.payloads) == 1:
                return {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "index": 0,
                                        "function": {
                                            "name": "execute_kali_command",
                                            "arguments": {
                                                "command": "pwd",
                                                "artifact_paths": ["reports/out.txt"],
                                            },
                                        },
                                    }
                                ],
                            }
                        }
                    ]
                }

            messages = cast(list[dict[str, object]], payload["messages"])
            assistant_message = next(
                message
                for message in messages
                if message.get("role") == "assistant" and message.get("tool_calls")
            )
            tool_message = next(message for message in messages if message.get("role") == "tool")

            assert assistant_message == {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "execute_kali_command",
                            "arguments": json.dumps(
                                {
                                    "command": "pwd",
                                    "artifact_paths": ["reports/out.txt"],
                                },
                                ensure_ascii=False,
                            ),
                        },
                    }
                ],
            }
            assert tool_message["tool_call_id"] == "call-1"
            assert tool_message["name"] == "execute_kali_command"
            assert json.loads(cast(str, tool_message["content"])) == {
                "tool": "execute_kali_command",
                "payload": {"status": "ok"},
            }
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "Final answer",
                        }
                    }
                ]
            }

    runtime = StubRuntime()

    async def execute_tool(tool_request: ToolCallRequest) -> ToolCallResult:
        assert tool_request.tool_name == "execute_kali_command"
        assert tool_request.arguments == {
            "command": "pwd",
            "timeout_seconds": None,
            "artifact_paths": ["reports/out.txt"],
        }
        return ToolCallResult(tool_name=tool_request.tool_name, payload={"status": "ok"})

    result = asyncio.run(
        runtime.generate_reply("Collect initial evidence", [], execute_tool=execute_tool)
    )

    assert result == "Final answer"
    assert len(runtime.payloads) == 2


def test_anthropic_runtime_streams_when_tools_are_enabled() -> None:
    settings = Settings.model_construct(
        anthropic_api_key="test-key",
        anthropic_api_base_url="https://example.test",
        anthropic_model="claude-demo",
    )

    class StreamingToolRuntime(AnthropicChatRuntime):
        def __init__(self) -> None:
            super().__init__(settings=settings)
            self.stream_payloads: list[dict[str, object]] = []
            self.stream_calls = 0

        async def _stream_completion(
            self,
            endpoint: str,
            headers: dict[str, str],
            payload: dict[str, object],
            callbacks: object,
        ) -> dict[str, object]:
            del endpoint, headers, callbacks
            self.stream_calls += 1
            self.stream_payloads.append(payload)
            if self.stream_calls == 1:
                return {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "execute_kali_command",
                            "input": {"command": "pwd"},
                        }
                    ]
                }
            return {"content": [{"type": "text", "text": "streamed final answer"}]}

        async def _request_completion(
            self,
            endpoint: str,
            headers: dict[str, str],
            payload: dict[str, object],
        ) -> dict[str, object]:
            del endpoint, headers, payload
            raise AssertionError(
                "_request_completion should not be used when callbacks are provided"
            )

    runtime = StreamingToolRuntime()

    async def execute_tool(tool_request: ToolCallRequest) -> ToolCallResult:
        return ToolCallResult(tool_name=tool_request.tool_name, payload={"status": "ok"})

    result = asyncio.run(
        runtime.generate_reply(
            "Collect initial evidence",
            [],
            execute_tool=execute_tool,
            callbacks=GenerationCallbacks(),
        )
    )

    assert result == "streamed final answer"
    assert runtime.stream_calls == 2
    assert all(payload["stream"] is True for payload in runtime.stream_payloads)
