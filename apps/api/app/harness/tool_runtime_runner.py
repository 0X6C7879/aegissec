from __future__ import annotations

import asyncio
import importlib
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.events import SessionEvent, SessionEventBroker, SessionEventType
from app.db.models import AssistantTranscriptSegmentKind, Message, Session, utc_now
from app.db.repositories import SessionRepository
from app.harness import events as harness_events
from app.services.chat_runtime import ChatRuntimeError, ToolCallRequest, ToolCallResult

_generation_events = importlib.import_module("app.harness.generation_events")
_semantic = importlib.import_module("app.harness.semantic")
_trace = importlib.import_module("app.harness.trace")
_transcript = importlib.import_module("app.harness.transcript")

publish_attack_graph_updated = _generation_events.publish_attack_graph_updated
publish_message_event = _generation_events.publish_message_event
publish_swarm_notifications = _generation_events.publish_swarm_notifications
drain_semantic_snapshot = _semantic.drain_semantic_snapshot
stage_semantic_deltas = _semantic.stage_semantic_deltas
stage_swarm_notification_semantics = _semantic.stage_swarm_notification_semantics
record_generation_step = _trace.record_generation_step
append_transcript_segment = _transcript.append_transcript_segment
find_transcript_segment = _transcript.find_transcript_segment
message_transcript_segments = _transcript.message_transcript_segments
update_transcript_segment = _transcript.update_transcript_segment


class AssistantTracePublisher(Protocol):
    def __call__(
        self,
        repository: SessionRepository,
        event_broker: SessionEventBroker,
        *,
        session_id: str,
        assistant_message: Message,
        entry: dict[str, object],
    ) -> Awaitable[None]: ...


@dataclass
class ToolRuntimeLifecycleRunner:
    session: Session
    assistant_message: Message
    repository: SessionRepository
    event_broker: SessionEventBroker
    session_state: Any | None
    publish_assistant_trace: AssistantTracePublisher

    async def publish_tool_started(
        self,
        tool_request: ToolCallRequest,
        *,
        tool_call_metadata: dict[str, object],
        governance_metadata: dict[str, object] | None,
        started_payload: dict[str, Any],
        trace_entry: dict[str, Any] | None = None,
    ) -> None:
        await self.publish_assistant_trace(
            self.repository,
            self.event_broker,
            session_id=self.session.id,
            assistant_message=self.assistant_message,
            entry={
                "state": "tool.started",
                "tool": tool_request.tool_name,
                "tool_call_id": tool_request.tool_call_id,
                **(dict(trace_entry) if trace_entry else {}),
                **({"governance": governance_metadata} if governance_metadata is not None else {}),
            },
        )
        append_transcript_segment(
            self.repository,
            assistant_message=self.assistant_message,
            kind=AssistantTranscriptSegmentKind.TOOL_CALL,
            status="running",
            title=tool_request.tool_name,
            text=(
                str(tool_request.arguments.get("command"))
                if isinstance(tool_request.arguments.get("command"), str)
                else None
            ),
            tool_name=tool_request.tool_name,
            tool_call_id=tool_request.tool_call_id,
            metadata_json=tool_call_metadata,
        )
        await publish_message_event(
            self.event_broker,
            event_type=SessionEventType.MESSAGE_UPDATED,
            session_id=self.session.id,
            message=self.assistant_message,
        )
        await self.event_broker.publish(
            SessionEvent(
                type=SessionEventType.TOOL_CALL_STARTED,
                session_id=self.session.id,
                payload=started_payload,
            )
        )
        await publish_attack_graph_updated(
            self.event_broker,
            session_id=self.session.id,
            assistant_message=self.assistant_message,
        )
        if self.assistant_message.generation_id is not None:
            record_generation_step(
                self.repository,
                assistant_message=self.assistant_message,
                kind="tool",
                phase="tool_running",
                status="running",
                state="started",
                label=tool_request.tool_name,
                tool_name=tool_request.tool_name,
                tool_call_id=tool_request.tool_call_id,
                command=(
                    str(tool_request.arguments.get("command"))
                    if isinstance(tool_request.arguments.get("command"), str)
                    else None
                ),
                metadata_json=tool_call_metadata,
            )

    async def publish_tool_failed(
        self,
        tool_request: ToolCallRequest,
        *,
        started_payload: dict[str, Any],
        error_message: str,
        error_artifacts: object | None = None,
    ) -> None:
        transcript_tool_call_metadata = dict(
            getattr(error_artifacts, "transcript_tool_call_metadata", {}) or {}
        )
        error_event_payload = dict(getattr(error_artifacts, "event_payload", {}) or {})
        error_trace_entry = dict(getattr(error_artifacts, "trace_entry", {}) or {})
        error_step_metadata = dict(getattr(error_artifacts, "step_metadata", {}) or {})
        semantic_snapshot = drain_semantic_snapshot(
            self.repository,
            assistant_message=self.assistant_message,
            session_state=self.session_state,
        )
        if self.assistant_message.generation_id is not None:
            tool_step = self.repository.get_open_generation_step(
                self.assistant_message.generation_id,
                kind="tool",
                tool_call_id=tool_request.tool_call_id,
            )
            if tool_step is not None:
                self.repository.update_generation_step(
                    tool_step,
                    phase="tool_result",
                    status="failed",
                    state="failed",
                    safe_summary=error_message,
                    ended_at=utc_now(),
                    metadata_json={
                        **dict(tool_step.metadata_json),
                        **error_step_metadata,
                        "error": error_message,
                    },
                )
        transcript_segments = message_transcript_segments(self.repository, self.assistant_message)
        tool_call_segment = find_transcript_segment(
            transcript_segments,
            kind=AssistantTranscriptSegmentKind.TOOL_CALL,
            tool_call_id=tool_request.tool_call_id,
        )
        if tool_call_segment is not None:
            update_transcript_segment(
                self.repository,
                assistant_message=self.assistant_message,
                segment=tool_call_segment,
                status="failed",
                metadata_json={
                    "error": error_message,
                    **transcript_tool_call_metadata,
                },
            )
        append_transcript_segment(
            self.repository,
            assistant_message=self.assistant_message,
            kind=AssistantTranscriptSegmentKind.ERROR,
            status="failed",
            title=tool_request.tool_name,
            text=error_message,
            tool_name=tool_request.tool_name,
            tool_call_id=tool_request.tool_call_id,
            metadata_json={"arguments": dict(tool_request.arguments), "error": error_message},
        )
        await self.publish_assistant_trace(
            self.repository,
            self.event_broker,
            session_id=self.session.id,
            assistant_message=self.assistant_message,
            entry={
                "state": "tool.failed",
                "tool": tool_request.tool_name,
                "tool_call_id": tool_request.tool_call_id,
                "error": error_message,
                **error_trace_entry,
                **({"semantic_state": semantic_snapshot} if semantic_snapshot else {}),
            },
        )
        await self.event_broker.publish(
            SessionEvent(
                type=SessionEventType.TOOL_CALL_FAILED,
                session_id=self.session.id,
                payload={
                    **started_payload,
                    "error": error_message,
                    **error_event_payload,
                    **harness_events.semantic_event_payload(semantic_snapshot),
                },
            )
        )
        await publish_message_event(
            self.event_broker,
            event_type=SessionEventType.MESSAGE_UPDATED,
            session_id=self.session.id,
            message=self.assistant_message,
        )
        await publish_attack_graph_updated(
            self.event_broker,
            session_id=self.session.id,
            assistant_message=self.assistant_message,
            semantic_snapshot=semantic_snapshot,
        )

    async def persist_tool_success(
        self,
        tool_request: ToolCallRequest,
        *,
        tool_result: Any,
        started_payload: dict[str, Any],
    ) -> ToolCallResult:
        stage_semantic_deltas(self.session_state, tool_result.semantic_deltas)
        swarm_notifications = tool_result.event_payload.get("swarm_notifications")
        if isinstance(swarm_notifications, list):
            stage_swarm_notification_semantics(
                self.session_state,
                [item for item in swarm_notifications if isinstance(item, dict)],
            )
        semantic_snapshot = drain_semantic_snapshot(
            self.repository,
            assistant_message=self.assistant_message,
            session_state=self.session_state,
        )
        transcript_segments = message_transcript_segments(self.repository, self.assistant_message)
        tool_call_segment = find_transcript_segment(
            transcript_segments,
            kind=AssistantTranscriptSegmentKind.TOOL_CALL,
            tool_call_id=tool_request.tool_call_id,
        )
        result_metadata = {
            **(
                dict(tool_result.transcript_result_metadata)
                if tool_result.transcript_result_metadata
                else {"result": tool_result.payload}
            ),
            **({"semantic_state": semantic_snapshot} if semantic_snapshot else {}),
        }
        if tool_call_segment is not None:
            update_transcript_segment(
                self.repository,
                assistant_message=self.assistant_message,
                segment=tool_call_segment,
                status="completed",
                metadata_json=(
                    dict(tool_result.transcript_tool_call_metadata)
                    if tool_result.transcript_tool_call_metadata
                    else None
                ),
            )
            append_transcript_segment(
                self.repository,
                assistant_message=self.assistant_message,
                kind=AssistantTranscriptSegmentKind.TOOL_RESULT,
                status=tool_result.status,
                title=tool_request.tool_name,
                text=None,
                tool_name=tool_request.tool_name,
                tool_call_id=tool_request.tool_call_id,
                metadata_json=result_metadata,
            )
        await self.publish_assistant_trace(
            self.repository,
            self.event_broker,
            session_id=self.session.id,
            assistant_message=self.assistant_message,
            entry={
                "state": "tool.finished",
                "tool": tool_request.tool_name,
                "tool_call_id": tool_request.tool_call_id,
                "orphan_tool_result": tool_call_segment is None,
                **dict(tool_result.trace_entry),
                **({"semantic_state": semantic_snapshot} if semantic_snapshot else {}),
            },
        )
        await self.event_broker.publish(
            SessionEvent(
                type=SessionEventType.TOOL_CALL_FINISHED,
                session_id=self.session.id,
                payload={
                    **started_payload,
                    **dict(tool_result.event_payload),
                    "result": tool_result.payload,
                    **harness_events.semantic_event_payload(semantic_snapshot),
                },
            )
        )
        if isinstance(swarm_notifications, list):
            await publish_swarm_notifications(
                self.event_broker,
                session_id=self.session.id,
                notifications=[item for item in swarm_notifications if isinstance(item, dict)],
            )
        await publish_message_event(
            self.event_broker,
            event_type=SessionEventType.MESSAGE_UPDATED,
            session_id=self.session.id,
            message=self.assistant_message,
        )
        await publish_attack_graph_updated(
            self.event_broker,
            session_id=self.session.id,
            assistant_message=self.assistant_message,
            semantic_snapshot=semantic_snapshot,
        )
        if self.assistant_message.generation_id is not None:
            tool_step = self.repository.get_open_generation_step(
                self.assistant_message.generation_id,
                kind="tool",
                tool_call_id=tool_request.tool_call_id,
            )
            if tool_step is not None:
                self.repository.update_generation_step(
                    tool_step,
                    phase="tool_result",
                    status="completed",
                    state="finished",
                    safe_summary=tool_result.safe_summary,
                    ended_at=utc_now(),
                    metadata_json={
                        **dict(tool_step.metadata_json),
                        **dict(tool_result.step_metadata),
                        "orphan_tool_result": tool_call_segment is None,
                        **({"semantic_state": semantic_snapshot} if semantic_snapshot else {}),
                    },
                )
        return ToolCallResult(
            tool_name=tool_request.tool_name,
            payload=tool_result.payload,
            tool_call_id=tool_request.tool_call_id,
            safe_summary=tool_result.safe_summary,
        )

    async def execute_readonly_parallel_phase(
        self,
        *,
        phase: Any,
        runtime: Any,
        executor_module: Any,
    ) -> list[tuple[int, ToolCallResult]]:
        phase_results: list[tuple[int, ToolCallResult]] = []
        for scheduled in phase.items:
            scheduled.prepared = await executor_module.apply_pre_tool_hooks(
                runtime=runtime,
                prepared=scheduled.prepared,
                tool_request=scheduled.tool_request,
            )
            await self.publish_tool_started(
                scheduled.tool_request,
                tool_call_metadata=scheduled.prepared.tool_call_metadata,
                governance_metadata=scheduled.prepared.governance_metadata,
                started_payload=scheduled.prepared.started_payload,
                trace_entry=scheduled.prepared.trace_entry,
            )

        raw_results = await asyncio.gather(
            *[
                executor_module.run_tool_with_hooks(
                    runtime=runtime,
                    prepared=scheduled.prepared,
                    tool_request=scheduled.tool_request,
                )
                for scheduled in phase.items
            ],
            return_exceptions=True,
        )

        for scheduled, raw_result in zip(phase.items, raw_results, strict=False):
            if isinstance(raw_result, Exception):
                error_artifacts = await executor_module.notify_tool_execution_error(
                    runtime=runtime,
                    prepared=scheduled.prepared,
                    tool_request=scheduled.tool_request,
                    error=raw_result,
                )
                await self.publish_tool_failed(
                    scheduled.tool_request,
                    started_payload=scheduled.prepared.started_payload,
                    error_message=str(raw_result),
                    error_artifacts=error_artifacts,
                )
                raise ChatRuntimeError(str(raw_result)) from raw_result
            phase_results.append(
                (
                    scheduled.order,
                    await self.persist_tool_success(
                        scheduled.tool_request,
                        tool_result=raw_result,
                        started_payload=scheduled.prepared.started_payload,
                    ),
                )
            )

        return phase_results

    async def execute_constrained_parallel_phase(
        self,
        *,
        phase: Any,
        runtime: Any,
        executor_module: Any,
        scheduling_module: Any,
    ) -> list[tuple[int, ToolCallResult]]:
        phase_results: list[tuple[int, ToolCallResult]] = []
        for scheduled in phase.items:
            scheduled.prepared = await executor_module.apply_pre_tool_hooks(
                runtime=runtime,
                prepared=scheduled.prepared,
                tool_request=scheduled.tool_request,
            )
            await self.publish_tool_started(
                scheduled.tool_request,
                tool_call_metadata=scheduled.prepared.tool_call_metadata,
                governance_metadata=scheduled.prepared.governance_metadata,
                started_payload=scheduled.prepared.started_payload,
                trace_entry=scheduled.prepared.trace_entry,
            )

        async def run_group(group: list[Any]) -> list[tuple[Any, Any]] | tuple[Any, Exception]:
            group_results: list[tuple[Any, Any]] = []
            for scheduled in group:
                try:
                    raw_result = await executor_module.run_tool_with_hooks(
                        runtime=runtime,
                        prepared=scheduled.prepared,
                        tool_request=scheduled.tool_request,
                    )
                except Exception as exc:  # noqa: BLE001
                    return scheduled, exc
                group_results.append((scheduled, raw_result))
            return group_results

        raw_group_results = await asyncio.gather(
            *[run_group(group) for group in scheduling_module.build_parallel_groups(phase)]
        )

        group_failures: list[tuple[Any, Exception]] = []

        for group_result in raw_group_results:
            if isinstance(group_result, tuple):
                group_failures.append(group_result)
                continue
            for scheduled, raw_result in group_result:
                phase_results.append(
                    (
                        scheduled.order,
                        await self.persist_tool_success(
                            scheduled.tool_request,
                            tool_result=raw_result,
                            started_payload=scheduled.prepared.started_payload,
                        ),
                    )
                )

        if group_failures:
            scheduled, error = group_failures[0]
            error_artifacts = await executor_module.notify_tool_execution_error(
                runtime=runtime,
                prepared=scheduled.prepared,
                tool_request=scheduled.tool_request,
                error=error,
            )
            await self.publish_tool_failed(
                scheduled.tool_request,
                started_payload=scheduled.prepared.started_payload,
                error_message=str(error),
                error_artifacts=error_artifacts,
            )
            raise ChatRuntimeError(str(error)) from error

        return phase_results
