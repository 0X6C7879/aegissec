from __future__ import annotations

import re
from uuid import uuid4

from app.db.models import (
    AssistantTranscriptSegment,
    AssistantTranscriptSegmentKind,
    Message,
    utc_now,
)
from app.db.repositories import SessionRepository
from app.services.chat_runtime import sanitize_assistant_content

THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
THINK_TAG_RE = re.compile(r"</?think\b[^>]*>", re.IGNORECASE)
_HIDDEN_STREAM_TAG_NAMES = {"invoke", "tool_call"}
_HIDDEN_STREAM_TAG_NAME_RE = re.compile(
    r"^<\s*(/)?\s*(?:[\w-]+:)?([a-z_]+)",
    re.IGNORECASE,
)
_VISIBLE_TRANSCRIPT_NOISE_PATTERNS = [
    re.compile(r"^assistant is analy[sz]ing", re.IGNORECASE),
    re.compile(r"^generation (started|completed|cancelled|canceled)\b", re.IGNORECASE),
    re.compile(r"^running\s+.+\.$", re.IGNORECASE),
    re.compile(r"^completed\s+.+\.$", re.IGNORECASE),
    re.compile(r"^命令已完成，状态：", re.IGNORECASE),
    re.compile(r"^已列出当前可用技能。$", re.IGNORECASE),
    re.compile(r"^已读取\s+.+\s+的技能内容。$", re.IGNORECASE),
]


def hidden_stream_tag_names() -> set[str]:
    return set(_HIDDEN_STREAM_TAG_NAMES)


def sanitize_persisted_assistant_text(content: str, *, fallback: str = "") -> str:
    return sanitize_assistant_content(
        content,
        strip_thinking=False,
        fallback_text=fallback,
    )


def is_visible_transcript_noise(content: str | None) -> bool:
    if not content:
        return True
    normalized = THINK_BLOCK_RE.sub(
        lambda match: THINK_TAG_RE.sub("", match.group(0)).strip() or " ",
        content,
    )
    collapsed = re.sub(r"\s+", " ", normalized).strip()
    if not collapsed:
        return True
    return any(pattern.search(collapsed) for pattern in _VISIBLE_TRANSCRIPT_NOISE_PATTERNS)


def match_hidden_stream_tag(fragment: str) -> tuple[str, bool, bool, bool] | None:
    match = _HIDDEN_STREAM_TAG_NAME_RE.match(fragment)
    if match is None:
        return None

    tag_name = match.group(2).lower()
    is_closing = bool(match.group(1))
    is_complete = ">" in fragment
    hidden_names = hidden_stream_tag_names()
    if is_complete:
        if tag_name not in hidden_names:
            return None
    elif not any(hidden_name.startswith(tag_name) for hidden_name in hidden_names):
        return None

    is_self_closing = is_complete and fragment.rstrip().endswith("/>")
    return tag_name, is_closing, is_complete, is_self_closing


def pop_hidden_stream_tag(hidden_stack: list[str], tag_name: str) -> None:
    for index in range(len(hidden_stack) - 1, -1, -1):
        if hidden_stack[index] == tag_name:
            del hidden_stack[index:]
            return


def project_visible_stream_content(content: str) -> str:
    if not content:
        return ""

    visible_chunks: list[str] = []
    hidden_stack: list[str] = []
    cursor = 0
    content_length = len(content)

    while cursor < content_length:
        tag_start = content.find("<", cursor)
        if tag_start < 0:
            if not hidden_stack:
                visible_chunks.append(content[cursor:])
            break

        if not hidden_stack and tag_start > cursor:
            visible_chunks.append(content[cursor:tag_start])

        tag_end = content.find(">", tag_start + 1)
        if tag_end < 0:
            trailing_fragment = content[tag_start:]
            hidden_match = match_hidden_stream_tag(trailing_fragment)
            if hidden_match is None and not hidden_stack:
                visible_chunks.append(trailing_fragment)
            break

        tag_fragment = content[tag_start : tag_end + 1]
        hidden_match = match_hidden_stream_tag(tag_fragment)
        if hidden_match is None:
            if not hidden_stack:
                visible_chunks.append(tag_fragment)
        else:
            tag_name, is_closing, _is_complete, is_self_closing = hidden_match
            if is_closing:
                pop_hidden_stream_tag(hidden_stack, tag_name)
            elif not is_self_closing:
                hidden_stack.append(tag_name)

        cursor = tag_end + 1

    return "".join(visible_chunks)


def message_transcript_segments(
    repository: SessionRepository, assistant_message: Message
) -> list[AssistantTranscriptSegment]:
    return repository.get_message_transcript(assistant_message)


def find_transcript_segment(
    segments: list[AssistantTranscriptSegment],
    *,
    kind: AssistantTranscriptSegmentKind | None = None,
    tool_call_id: str | None = None,
) -> AssistantTranscriptSegment | None:
    for segment in reversed(segments):
        if kind is not None and segment.kind != kind:
            continue
        if tool_call_id is not None and segment.tool_call_id != tool_call_id:
            continue
        return segment
    return None


def latest_transcript_segment(
    segments: list[AssistantTranscriptSegment],
) -> AssistantTranscriptSegment | None:
    if not segments:
        return None
    return max(segments, key=lambda segment: (segment.sequence, segment.recorded_at, segment.id))


def append_transcript_segment(
    repository: SessionRepository,
    *,
    assistant_message: Message,
    kind: AssistantTranscriptSegmentKind,
    status: str | None = None,
    title: str | None = None,
    text: str | None = None,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    metadata_json: dict[str, object] | None = None,
) -> AssistantTranscriptSegment:
    segments = message_transcript_segments(repository, assistant_message)
    next_sequence = max((segment.sequence for segment in segments), default=0) + 1
    now = utc_now()
    segment = AssistantTranscriptSegment(
        id=str(uuid4()),
        sequence=next_sequence,
        kind=kind,
        status=status,
        title=title,
        text=text,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        recorded_at=now,
        updated_at=now,
        metadata=metadata_json or {},
    )
    repository.append_message_transcript_segment(assistant_message, segment)
    return segment


def update_transcript_segment(
    repository: SessionRepository,
    *,
    assistant_message: Message,
    segment: AssistantTranscriptSegment,
    status: str | None = None,
    title: str | None = None,
    text: str | None = None,
    metadata_json: dict[str, object] | None = None,
) -> AssistantTranscriptSegment:
    merged_metadata = dict(segment.metadata_payload)
    if metadata_json is not None:
        merged_metadata.update(metadata_json)
    updated_segment = segment.model_copy(
        update={
            "status": status if status is not None else segment.status,
            "title": title if title is not None else segment.title,
            "text": text if text is not None else segment.text,
            "updated_at": utc_now(),
            "metadata_payload": merged_metadata,
        }
    )
    repository.update_message_transcript_segment(assistant_message, updated_segment)
    return updated_segment


def append_output_transcript_delta(
    repository: SessionRepository,
    *,
    assistant_message: Message,
    delta_text: str,
    status: str,
    append_to_current: bool,
) -> AssistantTranscriptSegment | None:
    transcript_segments = message_transcript_segments(repository, assistant_message)
    latest_segment = latest_transcript_segment(transcript_segments)
    if append_to_current and latest_segment is not None:
        if latest_segment.kind != AssistantTranscriptSegmentKind.OUTPUT:
            if not delta_text:
                return None
            return append_transcript_segment(
                repository,
                assistant_message=assistant_message,
                kind=AssistantTranscriptSegmentKind.OUTPUT,
                status=status,
                title=None,
                text=delta_text,
            )

        next_text = (
            f"{latest_segment.text or ''}{delta_text}" if delta_text else latest_segment.text
        )
        return update_transcript_segment(
            repository,
            assistant_message=assistant_message,
            segment=latest_segment,
            status=status,
            text=next_text,
        )

    if not delta_text:
        if (
            latest_segment is not None
            and latest_segment.kind == AssistantTranscriptSegmentKind.OUTPUT
        ):
            return update_transcript_segment(
                repository,
                assistant_message=assistant_message,
                segment=latest_segment,
                status=status,
            )
        return None

    return append_transcript_segment(
        repository,
        assistant_message=assistant_message,
        kind=AssistantTranscriptSegmentKind.OUTPUT,
        status=status,
        title=None,
        text=delta_text,
    )
