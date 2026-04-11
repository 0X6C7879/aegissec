from __future__ import annotations

import json
from typing import Any

from app.harness.messages import ToolCallRequest, ToolCallResult
from app.harness.query_engine import OpenAIQueryEngine


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
