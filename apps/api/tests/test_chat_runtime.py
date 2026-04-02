import asyncio
import json

from pytest import MonkeyPatch

from app.core.settings import Settings
from app.db.models import AttachmentMetadata, MessageRole, SkillAgentSummaryRead
from app.services.chat_runtime import (
    MAX_TOOL_STEPS,
    AnthropicChatRuntime,
    ConversationMessage,
    GenerationCallbacks,
    OpenAICompatibleChatRuntime,
    ToolCallRequest,
    ToolCallResult,
    get_chat_runtime,
)


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


def test_extract_message_content_strips_tool_protocol_markup_from_string_payload() -> None:
    content = (
        '<minimax:tool_call id="tool-1"><invoke name="agent-browser">'
        '{"task":"demo"}</invoke></minimax:tool_call>\n\n最终答复'
    )

    result = OpenAICompatibleChatRuntime._extract_message_content(content)

    assert result == "最终答复"


def test_assistant_message_for_history_strips_tool_protocol_markup() -> None:
    message = {
        "content": (
            '<minimax:tool_call id="tool-1"><invoke name="agent-browser">'
            '{"task":"demo"}</invoke></minimax:tool_call>最终答复'
        ),
        "tool_calls": [{"id": "call-1"}],
    }

    history_message = OpenAICompatibleChatRuntime._assistant_message_for_history(message)

    assert history_message == {
        "role": "assistant",
        "content": "最终答复",
        "tool_calls": [{"id": "call-1"}],
    }


def test_extract_tool_calls_supports_shell_and_skill_tools() -> None:
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
                    "name": "read_skill_content",
                    "arguments": {"skill_name_or_id": "adscan"},
                },
            },
        ]
    }

    tool_calls = OpenAICompatibleChatRuntime._extract_tool_calls(message)

    assert [(tool_call.tool_call_id, tool_call.tool_name) for tool_call in tool_calls] == [
        ("call-1", "execute_kali_command"),
        ("call-2", "list_available_skills"),
        ("call-3", "read_skill_content"),
    ]
    assert tool_calls[0].arguments == {
        "command": "pwd",
        "timeout_seconds": 15,
        "artifact_paths": ["reports/out.txt"],
    }
    assert tool_calls[1].arguments == {}
    assert tool_calls[2].arguments == {"skill_name_or_id": "adscan"}


def test_extract_tool_calls_coerces_loaded_skill_name_to_read_skill_content() -> None:
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
    assert tool_calls[0].tool_name == "read_skill_content"
    assert tool_calls[0].arguments == {"skill_name_or_id": "agent-browser"}


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
    assert request.tool_name == "read_skill_content"
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
