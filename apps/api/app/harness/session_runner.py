from __future__ import annotations

import asyncio
import importlib
import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession
from sqlmodel import col, select

from app.compat.mcp.service import MCPService
from app.compat.skills.service import SkillService
from app.core.events import SessionEventBroker, SessionEventType
from app.db.models import (
    AssistantTranscriptSegmentKind,
    ChatGeneration,
    GenerationStatus,
    Message,
    MessageStatus,
    Session,
    SessionStatus,
    attachments_from_storage,
    utc_now,
)
from app.db.repositories import SessionRepository
from app.harness.continuations import clear_generation_continuation_state
from app.services.capabilities import CapabilityFacade
from app.services.chat_runtime import (
    ChatRuntime,
    ChatRuntimeConfigurationError,
    ChatRuntimeError,
    GenerationCallbacks,
)
from app.services.runtime import RuntimeService
from app.services.session_generation import (
    GenerationCancelledError,
    SessionGenerationManager,
)

from . import generation_events as harness_generation_events
from . import semantic as harness_semantic
from . import trace as harness_trace
from . import transcript as harness_transcript


@dataclass(slots=True)
class HarnessGenerationPreparation:
    latest_message_text: str
    available_skills: list[Any]
    mcp_tool_inventory: list[dict[str, Any]]
    swarm_coordinator: Any
    prompt_assembly: Any


class ToolExecutorBuilder(Protocol):
    def __call__(
        self,
        *,
        session: Session,
        assistant_message: Message,
        repository: SessionRepository,
        event_broker: SessionEventBroker,
        generation_manager: SessionGenerationManager,
        runtime_service: RuntimeService,
        skill_service: SkillService,
        mcp_service: MCPService,
        mcp_tool_inventory: list[dict[str, Any]],
        session_state: Any,
        swarm_coordinator: Any,
    ) -> Any: ...


class AutoroutedSkillContextBuilder(Protocol):
    async def __call__(
        self,
        *,
        available_skills: list[Any],
        latest_message_text: str,
        recent_context_text: str,
        execute_tool: Any,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]: ...


class AssistantTracePublisher(Protocol):
    async def __call__(
        self,
        repository: SessionRepository,
        event_broker: SessionEventBroker,
        *,
        session_id: str,
        assistant_message: Message,
        entry: dict[str, object],
    ) -> None: ...


class AssistantSummaryPublisher(Protocol):
    async def __call__(
        self,
        repository: SessionRepository,
        event_broker: SessionEventBroker,
        *,
        session_id: str,
        assistant_message: Message,
        summary: str,
        semantic_snapshot: dict[str, Any] | None,
    ) -> None: ...


def chat_runtime_supports_mcp_tools(chat_runtime: ChatRuntime) -> bool:
    try:
        signature = inspect.signature(chat_runtime.generate_reply)
    except (TypeError, ValueError):
        return False
    return "mcp_tools" in signature.parameters


def chat_runtime_supports_harness_state(chat_runtime: ChatRuntime) -> bool:
    try:
        signature = inspect.signature(chat_runtime.generate_reply)
    except (TypeError, ValueError):
        return False
    return "harness_state" in signature.parameters


def build_swarm_coordinator(
    *,
    session: Any,
    chat_runtime: ChatRuntime,
    runtime_service: Any,
    skill_service: Any,
    mcp_service: Any,
    session_id: str,
    prompt_assembly: Any,
    generation_id: str,
    latest_message_text: str,
) -> Any:
    harness_swarm = importlib.import_module("app.harness.swarm")
    build_default_swarm_coordinator = harness_swarm.build_default_swarm_coordinator
    swarm_coordinator = build_default_swarm_coordinator(
        session_id=session_id,
        session_state=prompt_assembly.session_state,
        session=session,
        chat_runtime=chat_runtime,
        runtime_service=runtime_service,
        skill_service=skill_service,
        mcp_service=mcp_service,
    )
    swarm_coordinator.ensure_primary_agent(
        objective=latest_message_text,
        metadata={
            "generation_id": generation_id,
            "phase": prompt_assembly.session_state.current_phase,
        },
    )
    return swarm_coordinator


def build_generate_reply_kwargs(
    *,
    chat_runtime: ChatRuntime,
    prompt_assembly: Any,
    available_skills: list[Any],
    skill_context_prompt: str,
    execute_tool: Any,
    callbacks: GenerationCallbacks,
) -> dict[str, Any]:
    generate_reply_kwargs: dict[str, Any] = {
        "conversation_messages": prompt_assembly.conversation_messages,
        "available_skills": available_skills,
        "skill_context_prompt": skill_context_prompt,
        "execute_tool": execute_tool,
        "callbacks": callbacks,
    }
    if chat_runtime_supports_mcp_tools(chat_runtime):
        generate_reply_kwargs["mcp_tools"] = prompt_assembly.mcp_tool_inventory
    if chat_runtime_supports_harness_state(chat_runtime):
        generate_reply_kwargs["harness_state"] = prompt_assembly.session_state
    return generate_reply_kwargs


def recover_abandoned_generations(db_engine: Engine) -> int:
    current_time = utc_now()
    with DBSession(db_engine) as db_session:
        repository = SessionRepository(db_session)
        abandoned_ids = list(
            db_session.exec(
                select(ChatGeneration.id)
                .where(ChatGeneration.status == GenerationStatus.RUNNING)
                .where(
                    or_(
                        col(ChatGeneration.lease_expires_at).is_(None),
                        col(ChatGeneration.lease_expires_at) < current_time,
                    )
                )
                .order_by(col(ChatGeneration.created_at).asc(), col(ChatGeneration.id).asc())
            ).all()
        )
        recovered_count = repository.recover_abandoned_generations(now=current_time)
        for generation_id in abandoned_ids:
            generation = repository.get_generation(generation_id)
            if generation is None or generation.status != GenerationStatus.QUEUED:
                continue
            if generation.metadata_json.get("pending_continuation") is None and not isinstance(
                generation.metadata_json.get("pause_state"), dict
            ):
                continue
            assistant_message = repository.get_message(generation.assistant_message_id)
            clear_generation_continuation_state(
                repository,
                generation,
                assistant_message,
                abort_reason=(
                    "Generation recovery abandoned an in-memory continuation after restart."
                ),
            )
        return recovered_count


async def _mark_queued_generations_failed(
    db_engine: Engine,
    *,
    session_id: str,
    error_message: str,
) -> None:
    with DBSession(db_engine) as worker_db_session:
        repository = SessionRepository(worker_db_session)
        queued_generations = repository.list_generations(
            session_id,
            statuses={GenerationStatus.QUEUED},
        )
        for generation in queued_generations:
            assistant_message = repository.get_message(generation.assistant_message_id)
            clear_generation_continuation_state(
                repository,
                generation,
                assistant_message,
                abort_reason=error_message,
            )
            repository.mark_generation_failed(generation, error_message)
            if assistant_message is not None:
                repository.update_message(
                    assistant_message,
                    status=MessageStatus.FAILED,
                    error_message=error_message,
                )
                harness_trace.record_generation_step(
                    repository,
                    assistant_message=assistant_message,
                    kind="status",
                    phase="failed",
                    status="failed",
                    state="failed",
                    label="Generation failed",
                    safe_summary=error_message,
                    ended_at=utc_now(),
                    metadata_json={"generation_id": generation.id, "error": error_message},
                )
            repository.close_open_generation_steps(generation.id, status="failed", state="failed")


async def process_generation(
    *,
    db_engine: Engine,
    session_id: str,
    generation_id: str,
    event_broker: SessionEventBroker,
    generation_manager: SessionGenerationManager,
    chat_runtime: ChatRuntime,
    runtime_service: RuntimeService,
    skill_service: SkillService,
    mcp_service: MCPService,
    build_tool_executor: ToolExecutorBuilder,
    build_autorouted_skill_context: AutoroutedSkillContextBuilder,
    publish_assistant_trace: AssistantTracePublisher,
    publish_assistant_summary: AssistantSummaryPublisher,
    sanitize_persisted_assistant_text: Callable[[str], str],
    project_visible_stream_content: Callable[[str], str],
) -> str:
    active_session_state: Any | None = None
    with DBSession(db_engine) as worker_db_session:
        repository = SessionRepository(worker_db_session)
        generation = repository.get_generation(generation_id)
        if generation is None:
            raise ChatRuntimeError("Generation record was not found.")
        initial_assistant_message = repository.get_message(generation.assistant_message_id)
        if initial_assistant_message is None:
            raise ChatRuntimeError("Assistant message placeholder was not found.")

    await generation_manager.begin_generation(
        session_id,
        generation_id=generation_id,
        assistant_message_id=initial_assistant_message.id,
    )
    cancel_event = await generation_manager.get_cancel_event(session_id)

    try:
        with DBSession(db_engine) as worker_db_session:
            repository = SessionRepository(worker_db_session)
            session = repository.get_session(session_id)
            generation = repository.get_generation(generation_id)
            assistant_message = repository.get_message(initial_assistant_message.id)
            if session is None or generation is None or assistant_message is None:
                raise ChatRuntimeError("Generation state could not be reloaded.")
            loaded_assistant_message = assistant_message
            user_message = (
                repository.get_message(generation.user_message_id)
                if generation.user_message_id is not None
                else None
            )
            token_budget = generation.metadata_json.get("token_budget")
            total_token_budget = token_budget if isinstance(token_budget, int) else 12_000

            latest_message_text = (
                user_message.content
                if user_message is not None
                else loaded_assistant_message.content
            )
            capability_facade = CapabilityFacade(
                skill_service=skill_service,
                mcp_service=mcp_service,
            )
            harness_memory = importlib.import_module("app.harness.memory")
            harness_prompts = importlib.import_module("app.harness.prompts")
            HarnessMemoryService = harness_memory.HarnessMemoryService
            HarnessPromptAssembler = harness_prompts.HarnessPromptAssembler
            prompt_assembler = HarnessPromptAssembler(
                capability_facade=capability_facade,
                skill_service=skill_service,
                memory_service=HarnessMemoryService(),
            )
            prompt_assembly = prompt_assembler.build(
                session=session,
                repository=repository,
                user_message=user_message,
                assistant_message=loaded_assistant_message,
                branch_id=generation.branch_id,
                total_token_budget=total_token_budget,
            )
            active_session_state = prompt_assembly.session_state
            latest_message_text = prompt_assembly.latest_message_text
            available_skills = prompt_assembly.available_skills
            mcp_tool_inventory = prompt_assembly.mcp_tool_inventory
            swarm_coordinator = build_swarm_coordinator(
                session=session,
                chat_runtime=chat_runtime,
                runtime_service=runtime_service,
                skill_service=skill_service,
                mcp_service=mcp_service,
                session_id=session.id,
                prompt_assembly=prompt_assembly,
                generation_id=generation.id,
                latest_message_text=latest_message_text,
            )
            execute_tool = build_tool_executor(
                session=session,
                assistant_message=loaded_assistant_message,
                repository=repository,
                event_broker=event_broker,
                generation_manager=generation_manager,
                runtime_service=runtime_service,
                skill_service=skill_service,
                mcp_service=mcp_service,
                mcp_tool_inventory=mcp_tool_inventory,
                session_state=prompt_assembly.session_state,
                swarm_coordinator=swarm_coordinator,
            )
            conversation_history = prompt_assembly.conversation_history
            skill_context_prompt = prompt_assembly.skill_context_prompt

            await harness_generation_events.publish_generation_started(
                event_broker,
                session_id=session.id,
                generation_id=generation.id,
                user_message_id=generation.user_message_id,
                assistant_message_id=assistant_message.id,
                queued_prompt_count=repository.queue_size(session.id),
            )
            harness_trace.record_generation_step(
                repository,
                assistant_message=assistant_message,
                kind="status",
                phase="planning",
                status="running",
                state="started",
                label="开始生成",
                safe_summary=None,
                metadata_json={"generation_id": generation.id},
            )
            await publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=assistant_message,
                entry={"state": "generation.started", "generation_id": generation.id},
            )

            await publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=assistant_message,
                entry={"state": "skill.autoroute.started"},
            )
            (
                autorouted_skill_context,
                autoroute_trace_entries,
                autoroute_trace_entry,
            ) = await build_autorouted_skill_context(
                available_skills=available_skills,
                latest_message_text=latest_message_text,
                recent_context_text="\n\n".join(
                    message.content
                    for message in conversation_history[-6:]
                    if message.content.strip()
                ),
                execute_tool=execute_tool,
            )
            for trace_entry in autoroute_trace_entries:
                await publish_assistant_trace(
                    repository,
                    event_broker,
                    session_id=session.id,
                    assistant_message=assistant_message,
                    entry=trace_entry,
                )
            if autorouted_skill_context:
                skill_context_prompt = "\n\n".join(
                    part
                    for part in [skill_context_prompt, autorouted_skill_context]
                    if part.strip()
                )
            generation_metadata = dict(generation.metadata_json)
            existing_prompt_provenance = generation_metadata.get("prompt_provenance")
            prompt_provenance = (
                dict(existing_prompt_provenance)
                if isinstance(existing_prompt_provenance, dict)
                else {}
            )
            generation_metadata["prompt_provenance"] = {
                **prompt_provenance,
                "autorouted_skill": {
                    "state": autoroute_trace_entry.get("state"),
                    "skill": autoroute_trace_entry.get("skill"),
                    "confidence": autoroute_trace_entry.get("confidence"),
                    "reason": autoroute_trace_entry.get("reason"),
                    "top_candidate": autoroute_trace_entry.get("top_candidate"),
                    "candidates": autoroute_trace_entry.get("candidates", []),
                    "context_injected": bool(autorouted_skill_context),
                },
            }
            repository.update_generation(generation, metadata_json=generation_metadata)

            raw_streamed_content = loaded_assistant_message.content
            streamed_content = project_visible_stream_content(raw_streamed_content)

            async def on_text_delta(delta: str) -> None:
                nonlocal raw_streamed_content, streamed_content
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                raw_streamed_content += delta
                next_streamed_content = project_visible_stream_content(raw_streamed_content)
                if next_streamed_content == streamed_content:
                    return

                sanitized_delta = (
                    next_streamed_content[len(streamed_content) :]
                    if next_streamed_content.startswith(streamed_content)
                    else next_streamed_content
                )
                is_incremental_output = next_streamed_content.startswith(streamed_content)
                streamed_content = next_streamed_content
                repository.update_message(
                    loaded_assistant_message,
                    content=streamed_content,
                    status=MessageStatus.STREAMING,
                )
                harness_transcript.append_output_transcript_delta(
                    repository,
                    assistant_message=loaded_assistant_message,
                    delta_text=sanitized_delta,
                    status="running",
                    append_to_current=is_incremental_output,
                )
                output_step = harness_trace.get_or_create_output_step(
                    repository,
                    assistant_message=loaded_assistant_message,
                )
                if output_step is not None:
                    repository.append_generation_step_delta(output_step, sanitized_delta)
                await harness_generation_events.publish_message_event(
                    event_broker,
                    event_type=SessionEventType.MESSAGE_DELTA,
                    session_id=session.id,
                    message=loaded_assistant_message,
                    delta=sanitized_delta,
                )
                await harness_generation_events.publish_message_event(
                    event_broker,
                    event_type=SessionEventType.MESSAGE_UPDATED,
                    session_id=session.id,
                    message=loaded_assistant_message,
                )

            async def on_summary(summary: str) -> None:
                if summary.strip():
                    semantic_snapshot = harness_semantic.drain_semantic_snapshot(
                        repository,
                        assistant_message=loaded_assistant_message,
                        session_state=prompt_assembly.session_state,
                    )
                    await publish_assistant_summary(
                        repository,
                        event_broker,
                        session_id=session.id,
                        assistant_message=loaded_assistant_message,
                        summary=summary.strip(),
                        semantic_snapshot=semantic_snapshot,
                    )

            generate_reply_kwargs = build_generate_reply_kwargs(
                chat_runtime=chat_runtime,
                prompt_assembly=prompt_assembly,
                available_skills=available_skills,
                skill_context_prompt=skill_context_prompt,
                execute_tool=execute_tool,
                callbacks=GenerationCallbacks(
                    on_text_delta=on_text_delta,
                    on_summary=on_summary,
                    is_cancelled=cancel_event.is_set,
                ),
            )

            final_content = await chat_runtime.generate_reply(
                user_message.content
                if user_message is not None
                else loaded_assistant_message.content,
                []
                if user_message is None
                else attachments_from_storage(user_message.attachments_json),
                **generate_reply_kwargs,
            )
            final_content = sanitize_persisted_assistant_text(final_content) or (
                "模型已完成分析，但没有返回可展示的最终答复。"
            )

            if cancel_event.is_set():
                raise asyncio.CancelledError

            repository.update_message(
                loaded_assistant_message,
                content=final_content,
                status=MessageStatus.COMPLETED,
                error_message="",
            )
            semantic_snapshot = harness_semantic.drain_semantic_snapshot(
                repository,
                assistant_message=loaded_assistant_message,
                session_state=prompt_assembly.session_state,
            )
            final_delta = (
                final_content[len(streamed_content) :]
                if final_content.startswith(streamed_content)
                else final_content
            )
            final_is_incremental = final_content.startswith(streamed_content)
            harness_transcript.append_output_transcript_delta(
                repository,
                assistant_message=loaded_assistant_message,
                delta_text=final_delta,
                status="completed",
                append_to_current=final_is_incremental,
            )
            repository.mark_generation_completed(generation)
            if final_content != streamed_content:
                if final_delta:
                    await harness_generation_events.publish_message_event(
                        event_broker,
                        event_type=SessionEventType.MESSAGE_DELTA,
                        session_id=session.id,
                        message=loaded_assistant_message,
                        delta=final_delta,
                    )
                await harness_generation_events.publish_message_event(
                    event_broker,
                    event_type=SessionEventType.MESSAGE_UPDATED,
                    session_id=session.id,
                    message=loaded_assistant_message,
                )
            output_step = harness_trace.get_or_create_output_step(
                repository,
                assistant_message=loaded_assistant_message,
            )
            if output_step is not None:
                if final_content != output_step.delta_text:
                    repository.update_generation_step(output_step, delta_text=final_content)
                repository.update_generation_step(
                    output_step,
                    phase="synthesis",
                    status="completed",
                    state="completed",
                    ended_at=utc_now(),
                )
            await harness_generation_events.publish_attack_graph_updated(
                event_broker,
                session_id=session.id,
                assistant_message=loaded_assistant_message,
                semantic_snapshot=semantic_snapshot,
            )
            await publish_assistant_trace(
                repository,
                event_broker,
                session_id=session.id,
                assistant_message=loaded_assistant_message,
                entry={
                    "state": "generation.completed",
                    "generation_id": generation.id,
                    **({"semantic_state": semantic_snapshot} if semantic_snapshot else {}),
                },
            )
            await harness_generation_events.publish_message_event(
                event_broker,
                event_type=SessionEventType.MESSAGE_COMPLETED,
                session_id=session.id,
                message=loaded_assistant_message,
            )
            harness_trace.record_generation_step(
                repository,
                assistant_message=loaded_assistant_message,
                kind="status",
                phase="completed",
                status="completed",
                state="completed",
                label="生成完成",
                safe_summary=None,
                ended_at=utc_now(),
                metadata_json={"generation_id": generation.id},
            )
            repository.close_open_generation_steps(
                generation.id, status="completed", state="completed"
            )
            return loaded_assistant_message.id
    except asyncio.CancelledError as exc:
        with DBSession(db_engine) as worker_db_session:
            repository = SessionRepository(worker_db_session)
            generation = repository.get_generation(generation_id)
            assistant_message = None
            if generation is not None:
                assistant_message = repository.get_message(generation.assistant_message_id)
                clear_generation_continuation_state(
                    repository,
                    generation,
                    assistant_message,
                    abort_reason="Active generation was cancelled.",
                )
                repository.cancel_generation(
                    generation,
                    error_message="Active generation was cancelled.",
                )
            if assistant_message is not None:
                repository.update_message(
                    assistant_message,
                    status=MessageStatus.CANCELLED,
                    error_message="Active generation was cancelled.",
                )
                transcript_segments = harness_transcript.message_transcript_segments(
                    repository,
                    assistant_message,
                )
                output_segment = harness_transcript.find_transcript_segment(
                    transcript_segments,
                    kind=AssistantTranscriptSegmentKind.OUTPUT,
                )
                if output_segment is not None:
                    harness_transcript.update_transcript_segment(
                        repository,
                        assistant_message=assistant_message,
                        segment=output_segment,
                        status="cancelled",
                    )
                semantic_snapshot = harness_semantic.drain_semantic_snapshot(
                    repository,
                    assistant_message=assistant_message,
                    session_state=active_session_state,
                )
                await publish_assistant_trace(
                    repository,
                    event_broker,
                    session_id=session_id,
                    assistant_message=assistant_message,
                    entry={
                        "state": "generation.cancelled",
                        "generation_id": generation_id,
                        **({"semantic_state": semantic_snapshot} if semantic_snapshot else {}),
                    },
                )
                await harness_generation_events.publish_attack_graph_updated(
                    event_broker,
                    session_id=session_id,
                    assistant_message=assistant_message,
                    semantic_snapshot=semantic_snapshot,
                )
                harness_trace.record_generation_step(
                    repository,
                    assistant_message=assistant_message,
                    kind="status",
                    phase="cancelled",
                    status="cancelled",
                    state="cancelled",
                    label="生成已取消",
                    safe_summary=None,
                    ended_at=utc_now(),
                    metadata_json={"generation_id": generation_id},
                )
                repository.close_open_generation_steps(
                    generation_id,
                    status="cancelled",
                    state="cancelled",
                )
                await harness_generation_events.publish_generation_cancelled(
                    event_broker,
                    session_id=session_id,
                    generation_id=generation_id,
                    assistant_message_id=assistant_message.id,
                )
        raise GenerationCancelledError("Active generation was cancelled.") from exc
    except (ChatRuntimeConfigurationError, ChatRuntimeError) as exc:
        with DBSession(db_engine) as worker_db_session:
            repository = SessionRepository(worker_db_session)
            generation = repository.get_generation(generation_id)
            assistant_message = None
            if generation is not None:
                assistant_message = repository.get_message(generation.assistant_message_id)
                clear_generation_continuation_state(
                    repository,
                    generation,
                    assistant_message,
                    abort_reason=str(exc),
                )
                repository.mark_generation_failed(generation, str(exc))
            if assistant_message is not None:
                repository.update_message(
                    assistant_message,
                    status=MessageStatus.FAILED,
                    error_message=str(exc),
                )
                transcript_segments = harness_transcript.message_transcript_segments(
                    repository,
                    assistant_message,
                )
                output_segment = harness_transcript.find_transcript_segment(
                    transcript_segments,
                    kind=AssistantTranscriptSegmentKind.OUTPUT,
                )
                if output_segment is not None:
                    harness_transcript.update_transcript_segment(
                        repository,
                        assistant_message=assistant_message,
                        segment=output_segment,
                        status="failed",
                    )
                semantic_snapshot = harness_semantic.drain_semantic_snapshot(
                    repository,
                    assistant_message=assistant_message,
                    session_state=active_session_state,
                )
                await publish_assistant_trace(
                    repository,
                    event_broker,
                    session_id=session_id,
                    assistant_message=assistant_message,
                    entry={
                        "state": "generation.failed",
                        "generation_id": generation_id,
                        "error": str(exc),
                        **({"semantic_state": semantic_snapshot} if semantic_snapshot else {}),
                    },
                )
                await harness_generation_events.publish_attack_graph_updated(
                    event_broker,
                    session_id=session_id,
                    assistant_message=assistant_message,
                    semantic_snapshot=semantic_snapshot,
                )
                harness_trace.record_generation_step(
                    repository,
                    assistant_message=assistant_message,
                    kind="status",
                    phase="failed",
                    status="failed",
                    state="failed",
                    label="Generation failed",
                    safe_summary=str(exc),
                    ended_at=utc_now(),
                    metadata_json={"generation_id": generation_id, "error": str(exc)},
                )
                repository.close_open_generation_steps(
                    generation_id,
                    status="failed",
                    state="failed",
                )
                await harness_generation_events.publish_generation_failed(
                    event_broker,
                    session_id=session_id,
                    generation_id=generation_id,
                    assistant_message_id=assistant_message.id,
                    error_message=str(exc),
                )
        raise
    finally:
        await generation_manager.clear_current_generation(session_id, generation_id)


async def run_session_worker(
    *,
    db_engine: Engine,
    session_id: str,
    event_broker: SessionEventBroker,
    generation_manager: SessionGenerationManager,
    chat_runtime: ChatRuntime,
    runtime_service: RuntimeService,
    skill_service: SkillService,
    mcp_service: MCPService,
    build_tool_executor: ToolExecutorBuilder,
    build_autorouted_skill_context: AutoroutedSkillContextBuilder,
    publish_assistant_trace: AssistantTracePublisher,
    publish_assistant_summary: AssistantSummaryPublisher,
    sanitize_persisted_assistant_text: Callable[[str], str],
    project_visible_stream_content: Callable[[str], str],
) -> None:
    current_task = asyncio.current_task()
    if current_task is None:
        return
    worker_id = f"session-worker-{uuid4()}"

    terminal_error: ChatRuntimeError | ChatRuntimeConfigurationError | None = None

    try:
        while True:
            with DBSession(db_engine) as worker_db_session:
                repository = SessionRepository(worker_db_session)
                generation = repository.claim_next_generation(session_id, worker_id=worker_id)

            if generation is None:
                break

            try:
                assistant_message_id = await process_generation(
                    db_engine=db_engine,
                    session_id=session_id,
                    generation_id=generation.id,
                    event_broker=event_broker,
                    generation_manager=generation_manager,
                    chat_runtime=chat_runtime,
                    runtime_service=runtime_service,
                    skill_service=skill_service,
                    mcp_service=mcp_service,
                    build_tool_executor=build_tool_executor,
                    build_autorouted_skill_context=build_autorouted_skill_context,
                    publish_assistant_trace=publish_assistant_trace,
                    publish_assistant_summary=publish_assistant_summary,
                    sanitize_persisted_assistant_text=sanitize_persisted_assistant_text,
                    project_visible_stream_content=project_visible_stream_content,
                )
                await generation_manager.resolve_future(
                    session_id,
                    generation.id,
                    assistant_message_id,
                )
            except GenerationCancelledError as exc:
                await generation_manager.reject_future(session_id, generation.id, exc)
                continue
            except ChatRuntimeConfigurationError as exc:
                terminal_error = exc
                await generation_manager.reject_continuation_futures(session_id, exc)
                await generation_manager.reject_future(session_id, generation.id, exc)
                await generation_manager.reject_pending(session_id, exc)
                await _mark_queued_generations_failed(
                    db_engine,
                    session_id=session_id,
                    error_message=str(exc),
                )
                break
            except ChatRuntimeError as exc:
                terminal_error = exc
                await generation_manager.reject_continuation_futures(session_id, exc)
                await generation_manager.reject_future(session_id, generation.id, exc)
                await generation_manager.reject_pending(session_id, exc)
                await _mark_queued_generations_failed(
                    db_engine,
                    session_id=session_id,
                    error_message=str(exc),
                )
                break
            except Exception as exc:
                terminal_error = ChatRuntimeError(str(exc))
                await generation_manager.reject_continuation_futures(session_id, terminal_error)
                await generation_manager.reject_future(session_id, generation.id, terminal_error)
                await generation_manager.reject_pending(session_id, terminal_error)
                await _mark_queued_generations_failed(
                    db_engine,
                    session_id=session_id,
                    error_message=str(exc),
                )
                break
    finally:
        with DBSession(db_engine) as worker_db_session:
            repository = SessionRepository(worker_db_session)
            session = repository.get_session(session_id)
            if session is not None:
                if terminal_error is not None:
                    session = repository.update_session(session, status=SessionStatus.ERROR)
                    await harness_generation_events.publish_session_updated(
                        event_broker,
                        session,
                        error=str(terminal_error),
                    )
                elif (
                    session.status == SessionStatus.RUNNING
                    and repository.get_active_generation(session.id) is None
                ):
                    session = repository.update_session(session, status=SessionStatus.DONE)
                    await harness_generation_events.publish_session_updated(
                        event_broker,
                        session,
                        queued_prompt_count=repository.queue_size(session.id),
                    )
        await generation_manager.worker_finished(session_id, current_task)


async def start_worker_if_needed(
    *,
    db_session: DBSession,
    session_id: str,
    event_broker: SessionEventBroker,
    generation_manager: SessionGenerationManager,
    chat_runtime: ChatRuntime,
    runtime_service: RuntimeService,
    skill_service: SkillService,
    mcp_service: MCPService,
    build_tool_executor: ToolExecutorBuilder | None = None,
    build_autorouted_skill_context: AutoroutedSkillContextBuilder | None = None,
    publish_assistant_trace: AssistantTracePublisher | None = None,
    publish_assistant_summary: AssistantSummaryPublisher | None = None,
    sanitize_persisted_assistant_text: Callable[[str], str] | None = None,
    project_visible_stream_content: Callable[[str], str] | None = None,
) -> None:
    if not await generation_manager.should_start_worker(session_id):
        return
    if any(
        callback is None
        for callback in (
            build_tool_executor,
            build_autorouted_skill_context,
            publish_assistant_trace,
            publish_assistant_summary,
            sanitize_persisted_assistant_text,
            project_visible_stream_content,
        )
    ):
        routes_chat = importlib.import_module("app.api.routes_chat")
        if build_tool_executor is None:
            build_tool_executor = routes_chat._build_tool_executor
        if build_autorouted_skill_context is None:
            build_autorouted_skill_context = routes_chat._build_autorouted_skill_context
        if publish_assistant_trace is None:
            publish_assistant_trace = routes_chat._publish_assistant_trace
        if publish_assistant_summary is None:
            publish_assistant_summary = routes_chat._publish_assistant_summary
        if sanitize_persisted_assistant_text is None:
            sanitize_persisted_assistant_text = routes_chat._sanitize_persisted_assistant_text
        if project_visible_stream_content is None:
            project_visible_stream_content = routes_chat._project_visible_stream_content
    assert build_tool_executor is not None
    assert build_autorouted_skill_context is not None
    assert publish_assistant_trace is not None
    assert publish_assistant_summary is not None
    assert sanitize_persisted_assistant_text is not None
    assert project_visible_stream_content is not None
    db_engine = db_session.get_bind()
    if not isinstance(db_engine, Engine):
        raise RuntimeError("Database engine is unavailable.")
    worker_task = asyncio.create_task(
        run_session_worker(
            db_engine=db_engine,
            session_id=session_id,
            event_broker=event_broker,
            generation_manager=generation_manager,
            chat_runtime=chat_runtime,
            runtime_service=runtime_service,
            skill_service=skill_service,
            mcp_service=mcp_service,
            build_tool_executor=build_tool_executor,
            build_autorouted_skill_context=build_autorouted_skill_context,
            publish_assistant_trace=publish_assistant_trace,
            publish_assistant_summary=publish_assistant_summary,
            sanitize_persisted_assistant_text=sanitize_persisted_assistant_text,
            project_visible_stream_content=project_visible_stream_content,
        )
    )
    await generation_manager.attach_worker(session_id, worker_task)
