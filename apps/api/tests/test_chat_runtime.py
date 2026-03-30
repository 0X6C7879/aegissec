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
