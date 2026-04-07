from __future__ import annotations

from uuid import uuid4

from app.db.models import (
    AssistantTranscriptSegment,
    AssistantTranscriptSegmentKind,
    Message,
    utc_now,
)
from app.db.repositories import SessionRepository


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
