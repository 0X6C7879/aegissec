import json

from app.services.chat_runtime import OpenAICompatibleChatRuntime


def test_extract_message_content_strips_think_blocks_from_string_payload() -> None:
    content = "<think>internal reasoning</think>\n\n最终答复"

    result = OpenAICompatibleChatRuntime._extract_message_content(content)

    assert result == "最终答复"


def test_extract_message_content_strips_think_blocks_from_text_parts() -> None:
    content = [
        {"type": "text", "text": "<think>internal reasoning</think>"},
        {"type": "text", "text": "最终答复"},
    ]

    result = OpenAICompatibleChatRuntime._extract_message_content(content)

    assert result == "最终答复"


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
