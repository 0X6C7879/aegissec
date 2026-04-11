from __future__ import annotations

import json
from typing import Any

import pytest

from app.harness.messages import ChatRuntimeError, ToolCallRequest, ToolCallResult
from app.harness.query_engine import AnthropicQueryEngine, OpenAIQueryEngine


class _StubOpenAIProvider:
    def _require_configuration(self) -> tuple[str, str, str]:
        return ("test-key", "https://example.test", "demo-model")

    @staticmethod
    def _build_initial_messages(*args: Any, **kwargs: Any) -> list[dict[str, object]]:
        del args, kwargs
        return [{"role": "user", "content": "hello"}]

    @staticmethod
    def _assistant_message_for_history(message: dict[str, object]) -> dict[str, object]:
        return {
            "role": "assistant",
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls", []),
        }


class _StubAnthropicProvider:
    def _require_configuration(self) -> tuple[str, str, str]:
        return ("test-key", "https://example.test/anthropic", "claude-demo")

    @staticmethod
    def _build_messages_endpoint(base_url: str) -> str:
        return f"{base_url.rstrip('/')}/messages"

    @staticmethod
    def _build_initial_messages(*args: Any, **kwargs: Any) -> list[dict[str, object]]:
        del args, kwargs
        return [{"role": "user", "content": [{"type": "text", "text": "hello"}]}]


def test_openai_query_engine_append_tool_results_includes_tool_name() -> None:
    engine = OpenAIQueryEngine(
        provider=_StubOpenAIProvider(),
        content="hello",
        attachments=[],
        conversation_messages=None,
        available_skills=[],
        mcp_tools=None,
        skill_context_prompt=None,
        max_turns=1,
        system_prompt="system",
    )
    assistant_payload = {
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
    tool_calls = [
        ToolCallRequest(
            tool_name="execute_kali_command",
            tool_call_id="call-1",
            arguments={"command": "pwd"},
        )
    ]
    tool_results = [
        ToolCallResult(tool_name="execute_kali_command", payload={"stdout": "/workspace"})
    ]

    engine.append_tool_results(
        assistant_payload=assistant_payload,
        tool_calls=tool_calls,
        tool_results=tool_results,
    )

    tool_message = engine.messages[-1]
    assert tool_message["role"] == "tool"
    assert tool_message["tool_call_id"] == "call-1"
    assert tool_message["name"] == "execute_kali_command"
    assert json.loads(str(tool_message["content"])) == {
        "tool": "execute_kali_command",
        "payload": {"stdout": "/workspace"},
    }


def test_openai_query_engine_append_tool_results_rejects_orphan_tool_result_ids() -> None:
    engine = OpenAIQueryEngine(
        provider=_StubOpenAIProvider(),
        content="hello",
        attachments=[],
        conversation_messages=None,
        available_skills=[],
        mcp_tools=None,
        skill_context_prompt=None,
        max_turns=1,
        system_prompt="system",
    )
    original_messages = list(engine.messages)
    assistant_payload = {
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
    tool_calls = [
        ToolCallRequest(
            tool_name="execute_kali_command",
            tool_call_id="call-1",
            arguments={"command": "pwd"},
        )
    ]
    tool_results = [
        ToolCallResult(
            tool_name="execute_kali_command",
            tool_call_id="call-2",
            payload={"stdout": "/workspace"},
        )
    ]

    with pytest.raises(ChatRuntimeError, match="tool_call_id"):
        engine.append_tool_results(
            assistant_payload=assistant_payload,
            tool_calls=tool_calls,
            tool_results=tool_results,
        )

    assert engine.messages == original_messages


def test_openai_query_engine_append_tool_results_summarizes_large_payload_for_history() -> None:
    engine = OpenAIQueryEngine(
        provider=_StubOpenAIProvider(),
        content="hello",
        attachments=[],
        conversation_messages=None,
        available_skills=[],
        mcp_tools=None,
        skill_context_prompt=None,
        max_turns=1,
        system_prompt="system",
    )
    assistant_payload = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "execute_kali_command",
                    "arguments": json.dumps({"command": "curl -s https://target.test"}),
                },
            }
        ],
    }
    large_stdout = "<!DOCTYPE html>" + ("A" * 6_000)
    tool_calls = [
        ToolCallRequest(
            tool_name="execute_kali_command",
            tool_call_id="call-1",
            arguments={"command": "curl -s https://target.test"},
        )
    ]
    tool_results = [
        ToolCallResult(
            tool_name="execute_kali_command",
            tool_call_id="call-1",
            safe_summary="命令已完成，状态：completed。",
            payload={
                "status": "completed",
                "command": "curl -s https://target.test",
                "exit_code": 0,
                "stdout": large_stdout,
                "stderr": "",
                "artifacts": ["reports/target.html"],
            },
        )
    ]

    engine.append_tool_results(
        assistant_payload=assistant_payload,
        tool_calls=tool_calls,
        tool_results=tool_results,
    )

    tool_message = engine.messages[-1]
    rendered = json.loads(str(tool_message["content"]))
    assert rendered["tool"] == "execute_kali_command"
    payload_summary = rendered["payload_summary"]
    assert payload_summary["truncated"] is True
    assert payload_summary["summary"] == "命令已完成，状态：completed。"
    assert payload_summary["stdout_chars"] == len(large_stdout)
    assert payload_summary["stdout_preview"].startswith("<!DOCTYPE html>")
    assert payload_summary["artifact_count"] == 1
    assert len(str(tool_message["content"])) < len(large_stdout)


def test_anthropic_query_engine_append_tool_results_rejects_orphan_tool_result_ids() -> None:
    engine = AnthropicQueryEngine(
        provider=_StubAnthropicProvider(),
        content="hello",
        attachments=[],
        conversation_messages=None,
        available_skills=[],
        mcp_tools=None,
        skill_context_prompt=None,
        max_turns=1,
        system_prompt="system",
    )
    original_messages = list(engine.messages)
    assistant_payload = {
        "content": [
            {
                "type": "tool_use",
                "id": "call-1",
                "name": "execute_kali_command",
                "input": {"command": "pwd"},
            }
        ]
    }
    tool_calls = [
        ToolCallRequest(
            tool_name="execute_kali_command",
            tool_call_id="call-1",
            arguments={"command": "pwd"},
        )
    ]
    tool_results = [
        ToolCallResult(
            tool_name="execute_kali_command",
            tool_call_id="call-2",
            payload={"stdout": "/workspace"},
        )
    ]

    with pytest.raises(ChatRuntimeError, match="tool_call_id"):
        engine.append_tool_results(
            assistant_payload=assistant_payload,
            tool_calls=tool_calls,
            tool_results=tool_results,
        )

    assert engine.messages == original_messages


def test_anthropic_query_engine_append_tool_results_summarizes_large_payload_for_history() -> None:
    engine = AnthropicQueryEngine(
        provider=_StubAnthropicProvider(),
        content="hello",
        attachments=[],
        conversation_messages=None,
        available_skills=[],
        mcp_tools=None,
        skill_context_prompt=None,
        max_turns=1,
        system_prompt="system",
    )
    assistant_payload = {
        "content": [
            {
                "type": "tool_use",
                "id": "call-1",
                "name": "execute_kali_command",
                "input": {"command": "curl -s https://target.test"},
            }
        ]
    }
    large_stdout = "<!DOCTYPE html>" + ("A" * 6_000)
    tool_calls = [
        ToolCallRequest(
            tool_name="execute_kali_command",
            tool_call_id="call-1",
            arguments={"command": "curl -s https://target.test"},
        )
    ]
    tool_results = [
        ToolCallResult(
            tool_name="execute_kali_command",
            tool_call_id="call-1",
            safe_summary="命令已完成，状态：completed。",
            payload={
                "status": "completed",
                "command": "curl -s https://target.test",
                "exit_code": 0,
                "stdout": large_stdout,
                "stderr": "",
                "artifacts": ["reports/target.html"],
            },
        )
    ]

    engine.append_tool_results(
        assistant_payload=assistant_payload,
        tool_calls=tool_calls,
        tool_results=tool_results,
    )

    user_message = engine.messages[-1]
    content_blocks = user_message["content"]
    assert isinstance(content_blocks, list)
    rendered = json.loads(str(content_blocks[0]["content"]))
    assert rendered["tool"] == "execute_kali_command"
    payload_summary = rendered["payload_summary"]
    assert payload_summary["truncated"] is True
    assert payload_summary["summary"] == "命令已完成，状态：completed。"
    assert payload_summary["stdout_chars"] == len(large_stdout)
    assert payload_summary["stdout_preview"].startswith("<!DOCTYPE html>")
