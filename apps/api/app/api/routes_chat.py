from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlmodel import Session as DBSession

from app.compat.mcp.service import MCPService, get_mcp_service
from app.compat.skills.service import (
    SkillContentReadError,
    SkillLookupError,
    SkillService,
    get_skill_service,
)
from app.core.events import SessionEvent, SessionEventBroker, SessionEventType, get_event_broker
from app.db.models import (
    ChatRequest,
    ChatResponse,
    MessageRole,
    RuntimeExecuteRequest,
    RuntimePolicy,
    Session,
    SessionStatus,
    attachments_to_storage,
    to_message_read,
    to_session_read,
)
from app.db.repositories import SessionRepository
from app.db.session import get_db_session
from app.services.capabilities import CapabilityFacade
from app.services.chat_runtime import (
    ChatRuntime,
    ChatRuntimeConfigurationError,
    ChatRuntimeError,
    GenerationCallbacks,
    ToolCallRequest,
    ToolCallResult,
    get_chat_runtime,
)
from app.services.runtime import (
    RuntimeArtifactPathError,
    RuntimeOperationError,
    RuntimePolicyViolationError,
    RuntimeService,
    get_runtime_service,
)
from app.services.session_generation import (
    GenerationCancelledError,
    QueuedPrompt,
    SessionGenerationManager,
    get_generation_manager,
)

router = APIRouter(prefix="/api/sessions", tags=["chat"])

SAFE_THINKING_SUMMARY = "Assistant is analyzing the request and preparing a response."


def _get_session_or_404(repository: SessionRepository, session_id: str) -> Session:
    session = repository.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


async def _publish_session_updated(
    event_broker: SessionEventBroker,
    session: Session,
    *,
    error: str | None = None,
    queued_prompt_count: int | None = None,
) -> None:
    payload: dict[str, Any] = {"title": session.title, "status": session.status.value}
    if error is not None:
        payload["error"] = error
    if queued_prompt_count is not None:
        payload["queued_prompt_count"] = queued_prompt_count

    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_UPDATED,
            session_id=session.id,
            payload=payload,
        )
    )


async def _publish_message_event(
    event_broker: SessionEventBroker,
    *,
    event_type: SessionEventType,
    session_id: str,
    message_id: str,
    role: str,
    content: str,
    attachments: list[dict[str, str | int | None]],
    created_at: str,
) -> None:
    await event_broker.publish(
        SessionEvent(
            type=event_type,
            session_id=session_id,
            payload={
                "message_id": message_id,
                "role": role,
                "content": content,
                "attachments": attachments,
                "created_at": created_at,
            },
        )
    )


async def _publish_generation_started(
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    generation_id: str,
    user_message_id: str,
    assistant_message_id: str,
    queued_prompt_count: int,
) -> None:
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.GENERATION_STARTED,
            session_id=session_id,
            payload={
                "generation_id": generation_id,
                "user_message_id": user_message_id,
                "message_id": assistant_message_id,
                "queued_prompt_count": queued_prompt_count,
            },
        )
    )


async def _publish_generation_cancelled(
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    generation_id: str,
    assistant_message_id: str,
) -> None:
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.GENERATION_CANCELLED,
            session_id=session_id,
            payload={"generation_id": generation_id, "message_id": assistant_message_id},
        )
    )


async def _publish_assistant_summary(
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    assistant_message_id: str,
    summary: str,
) -> None:
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.ASSISTANT_SUMMARY,
            session_id=session_id,
            payload={"message_id": assistant_message_id, "summary": summary},
        )
    )


def _build_tool_executor(
    *,
    session: Session,
    event_broker: SessionEventBroker,
    runtime_service: RuntimeService,
    skill_service: SkillService,
    mcp_service: MCPService,
) -> Any:
    available_skills = skill_service.list_loaded_skills_for_agent()
    del mcp_service

    async def execute_tool(tool_request: ToolCallRequest) -> ToolCallResult:
        started_payload: dict[str, Any] = {
            "tool": tool_request.tool_name,
            "tool_call_id": tool_request.tool_call_id,
            "arguments": tool_request.arguments,
        }
        if tool_request.tool_name == "execute_kali_command":
            started_payload.update(
                {
                    "command": tool_request.arguments.get("command"),
                    "timeout_seconds": tool_request.arguments.get("timeout_seconds"),
                    "artifact_paths": tool_request.arguments.get("artifact_paths", []),
                }
            )

        await event_broker.publish(
            SessionEvent(
                type=SessionEventType.TOOL_CALL_STARTED,
                session_id=session.id,
                payload=started_payload,
            )
        )

        async def publish_tool_failed(error_message: str) -> None:
            await event_broker.publish(
                SessionEvent(
                    type=SessionEventType.TOOL_CALL_FAILED,
                    session_id=session.id,
                    payload={**started_payload, "error": error_message},
                )
            )

        if tool_request.tool_name == "execute_kali_command":
            command = tool_request.arguments.get("command")
            timeout_seconds = tool_request.arguments.get("timeout_seconds")
            artifact_paths = tool_request.arguments.get("artifact_paths", [])
            if not isinstance(command, str) or not command.strip():
                await publish_tool_failed("Invalid command for execute_kali_command.")
                raise ChatRuntimeError("Invalid command for execute_kali_command.")
            if timeout_seconds is not None and not isinstance(timeout_seconds, int):
                await publish_tool_failed("Invalid timeout for execute_kali_command.")
                raise ChatRuntimeError("Invalid timeout for execute_kali_command.")
            if not isinstance(artifact_paths, list) or not all(
                isinstance(item, str) for item in artifact_paths
            ):
                await publish_tool_failed("Invalid artifact paths for execute_kali_command.")
                raise ChatRuntimeError("Invalid artifact paths for execute_kali_command.")

            try:
                run = runtime_service.execute(
                    RuntimeExecuteRequest(
                        command=command,
                        timeout_seconds=timeout_seconds,
                        session_id=session.id,
                        artifact_paths=artifact_paths,
                    ),
                    runtime_policy=RuntimePolicy.model_validate(session.runtime_policy_json or {}),
                )
            except ValidationError as exc:
                await publish_tool_failed(f"Invalid runtime policy: {exc.errors()[0]['msg']}")
                raise ChatRuntimeError("Invalid runtime policy for this session.") from exc
            except (
                RuntimeArtifactPathError,
                RuntimeOperationError,
                RuntimePolicyViolationError,
            ) as exc:
                await publish_tool_failed(str(exc))
                raise ChatRuntimeError(str(exc)) from exc

            command_result_payload: dict[str, Any] = {
                "status": run.status.value,
                "exit_code": run.exit_code,
                "stdout": run.stdout,
                "stderr": run.stderr,
                "artifacts": [artifact.relative_path for artifact in run.artifacts],
            }
            await event_broker.publish(
                SessionEvent(
                    type=SessionEventType.TOOL_CALL_FINISHED,
                    session_id=session.id,
                    payload={
                        **started_payload,
                        "tool": tool_request.tool_name,
                        "tool_call_id": tool_request.tool_call_id,
                        "run_id": run.id,
                        "command": run.command,
                        "status": run.status.value,
                        "exit_code": run.exit_code,
                        "requested_timeout_seconds": run.requested_timeout_seconds,
                        "stdout": run.stdout,
                        "stderr": run.stderr,
                        "created_at": run.created_at.isoformat(),
                        "artifact_paths": [artifact.relative_path for artifact in run.artifacts],
                        "result": command_result_payload,
                    },
                )
            )
            return ToolCallResult(tool_name=tool_request.tool_name, payload=command_result_payload)

        if tool_request.tool_name == "list_available_skills":
            skills_result_payload: dict[str, Any] = {
                "skills": [skill.model_dump(mode="json") for skill in available_skills],
            }
            await event_broker.publish(
                SessionEvent(
                    type=SessionEventType.TOOL_CALL_FINISHED,
                    session_id=session.id,
                    payload={**started_payload, "result": skills_result_payload},
                )
            )
            return ToolCallResult(tool_name=tool_request.tool_name, payload=skills_result_payload)

        if tool_request.tool_name == "read_skill_content":
            skill_name_or_id = tool_request.arguments.get("skill_name_or_id")
            if not isinstance(skill_name_or_id, str) or not skill_name_or_id.strip():
                await publish_tool_failed("read_skill_content requires a valid skill identifier.")
                raise ChatRuntimeError("read_skill_content requires a valid skill identifier.")

            try:
                skill_content = skill_service.read_skill_content_by_name_or_directory_name(
                    skill_name_or_id
                )
            except (SkillLookupError, SkillContentReadError) as exc:
                await publish_tool_failed(str(exc))
                raise ChatRuntimeError(str(exc)) from exc

            skill_result_payload: dict[str, Any] = {"skill": skill_content.model_dump(mode="json")}
            await event_broker.publish(
                SessionEvent(
                    type=SessionEventType.TOOL_CALL_FINISHED,
                    session_id=session.id,
                    payload={**started_payload, "result": skill_result_payload},
                )
            )
            return ToolCallResult(tool_name=tool_request.tool_name, payload=skill_result_payload)

        error_message = f"Unsupported tool requested: {tool_request.tool_name}."
        await publish_tool_failed(error_message)
        raise ChatRuntimeError(error_message)

    return execute_tool


async def _process_prompt(
    *,
    db_engine: Engine,
    session_id: str,
    queued_prompt: QueuedPrompt,
    event_broker: SessionEventBroker,
    generation_manager: SessionGenerationManager,
    chat_runtime: ChatRuntime,
    runtime_service: RuntimeService,
    skill_service: SkillService,
    mcp_service: MCPService,
) -> str:
    generation_id = str(uuid4())
    queued_prompt_count = await generation_manager.begin_generation(
        session_id,
        generation_id=generation_id,
        assistant_message_id=queued_prompt.assistant_message_id,
    )
    cancel_event = await generation_manager.get_cancel_event(session_id)

    try:
        with DBSession(db_engine) as worker_db_session:
            repository = SessionRepository(worker_db_session)
            session = _get_session_or_404(repository, session_id)
            assistant_message = repository.get_message(queued_prompt.assistant_message_id)
            if assistant_message is None:
                raise ChatRuntimeError("Assistant message placeholder was not found.")

            available_skills = skill_service.list_loaded_skills_for_agent()
            capability_facade = CapabilityFacade(
                skill_service=skill_service, mcp_service=mcp_service
            )
            skill_context_prompt = capability_facade.build_skill_prompt_fragment()
            execute_tool = _build_tool_executor(
                session=session,
                event_broker=event_broker,
                runtime_service=runtime_service,
                skill_service=skill_service,
                mcp_service=mcp_service,
            )

            await _publish_generation_started(
                event_broker,
                session_id=session.id,
                generation_id=generation_id,
                user_message_id=queued_prompt.user_message_id,
                assistant_message_id=assistant_message.id,
                queued_prompt_count=queued_prompt_count,
            )
            await _publish_assistant_summary(
                event_broker,
                session_id=session.id,
                assistant_message_id=assistant_message.id,
                summary=SAFE_THINKING_SUMMARY,
            )

            streamed_content = assistant_message.content

            async def on_text_delta(delta: str) -> None:
                nonlocal streamed_content
                if cancel_event.is_set():
                    raise asyncio.CancelledError
                streamed_content += delta
                repository.update_message_content(assistant_message, streamed_content)
                await _publish_message_event(
                    event_broker,
                    event_type=SessionEventType.MESSAGE_UPDATED,
                    session_id=session.id,
                    message_id=assistant_message.id,
                    role=assistant_message.role.value,
                    content=streamed_content,
                    attachments=assistant_message.attachments_json,
                    created_at=assistant_message.created_at.isoformat(),
                )

            async def on_summary(summary: str) -> None:
                if summary.strip():
                    await _publish_assistant_summary(
                        event_broker,
                        session_id=session.id,
                        assistant_message_id=assistant_message.id,
                        summary=summary.strip(),
                    )

            final_content = await chat_runtime.generate_reply(
                queued_prompt.content,
                queued_prompt.attachments,
                available_skills=available_skills,
                skill_context_prompt=skill_context_prompt,
                execute_tool=execute_tool,
                callbacks=GenerationCallbacks(
                    on_text_delta=on_text_delta,
                    on_summary=on_summary,
                    is_cancelled=cancel_event.is_set,
                ),
            )

            if cancel_event.is_set():
                raise asyncio.CancelledError

            repository.update_message_content(assistant_message, final_content)
            if final_content != streamed_content:
                await _publish_message_event(
                    event_broker,
                    event_type=SessionEventType.MESSAGE_UPDATED,
                    session_id=session.id,
                    message_id=assistant_message.id,
                    role=assistant_message.role.value,
                    content=final_content,
                    attachments=assistant_message.attachments_json,
                    created_at=assistant_message.created_at.isoformat(),
                )
            return assistant_message.id
    except asyncio.CancelledError as exc:
        await _publish_generation_cancelled(
            event_broker,
            session_id=session_id,
            generation_id=generation_id,
            assistant_message_id=queued_prompt.assistant_message_id,
        )
        raise GenerationCancelledError("Active generation was cancelled.") from exc
    finally:
        await generation_manager.clear_current_generation(session_id, generation_id)


async def _run_session_worker(
    *,
    db_engine: Engine,
    session_id: str,
    event_broker: SessionEventBroker,
    generation_manager: SessionGenerationManager,
    chat_runtime: ChatRuntime,
    runtime_service: RuntimeService,
    skill_service: SkillService,
    mcp_service: MCPService,
) -> None:
    current_task = asyncio.current_task()
    if current_task is None:
        return

    terminal_error: (
        ChatRuntimeError | ChatRuntimeConfigurationError | GenerationCancelledError | None
    ) = None
    terminal_status: SessionStatus | None = None

    try:
        while True:
            queued_prompt = await generation_manager.pop_next_prompt(session_id)
            if queued_prompt is None:
                if await generation_manager.finish_if_idle(session_id, current_task):
                    break
                await asyncio.sleep(0)
                continue

            try:
                assistant_message_id = await _process_prompt(
                    db_engine=db_engine,
                    session_id=session_id,
                    queued_prompt=queued_prompt,
                    event_broker=event_broker,
                    generation_manager=generation_manager,
                    chat_runtime=chat_runtime,
                    runtime_service=runtime_service,
                    skill_service=skill_service,
                    mcp_service=mcp_service,
                )
                if not queued_prompt.response_future.done():
                    queued_prompt.response_future.set_result(assistant_message_id)
            except GenerationCancelledError as exc:
                terminal_error = exc
                terminal_status = SessionStatus.CANCELLED
                if not queued_prompt.response_future.done():
                    queued_prompt.response_future.set_exception(exc)
                await generation_manager.fail_pending(session_id, exc)
                break
            except ChatRuntimeConfigurationError as exc:
                terminal_error = exc
                terminal_status = SessionStatus.ERROR
                if not queued_prompt.response_future.done():
                    queued_prompt.response_future.set_exception(exc)
                await generation_manager.fail_pending(session_id, exc)
                break
            except ChatRuntimeError as exc:
                terminal_error = exc
                terminal_status = SessionStatus.ERROR
                if not queued_prompt.response_future.done():
                    queued_prompt.response_future.set_exception(exc)
                await generation_manager.fail_pending(session_id, exc)
                break
    finally:
        with DBSession(db_engine) as worker_db_session:
            repository = SessionRepository(worker_db_session)
            session = repository.get_session(session_id)
            if session is not None:
                if terminal_status is None:
                    session = repository.update_session(session, status=SessionStatus.DONE)
                    await _publish_session_updated(event_broker, session)
                elif terminal_status == SessionStatus.CANCELLED:
                    session = repository.update_session(session, status=SessionStatus.CANCELLED)
                    await _publish_session_updated(event_broker, session)
                else:
                    session = repository.update_session(session, status=SessionStatus.ERROR)
                    await _publish_session_updated(
                        event_broker,
                        session,
                        error=str(terminal_error) if terminal_error is not None else None,
                    )

        await generation_manager.finish_if_idle(session_id, current_task)


@router.post("/{session_id}/chat", response_model=ChatResponse)
async def create_chat_message(
    session_id: str,
    payload: ChatRequest,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    chat_runtime: ChatRuntime = Depends(get_chat_runtime),
    runtime_service: RuntimeService = Depends(get_runtime_service),
    skill_service: SkillService = Depends(get_skill_service),
    mcp_service: MCPService = Depends(get_mcp_service),
    generation_manager: SessionGenerationManager = Depends(get_generation_manager),
) -> ChatResponse:
    repository = SessionRepository(db_session)
    session = _get_session_or_404(repository, session_id)

    if session.status != SessionStatus.RUNNING:
        session = repository.update_session(session, status=SessionStatus.RUNNING)
        await _publish_session_updated(event_broker, session)

    user_message = repository.create_message(
        session=session,
        role=MessageRole.USER,
        content=payload.content,
        attachments=attachments_to_storage(payload.attachments),
    )
    await _publish_message_event(
        event_broker,
        event_type=SessionEventType.MESSAGE_CREATED,
        session_id=session.id,
        message_id=user_message.id,
        role=user_message.role.value,
        content=user_message.content,
        attachments=user_message.attachments_json,
        created_at=user_message.created_at.isoformat(),
    )

    assistant_message = repository.create_message(
        session=session,
        role=MessageRole.ASSISTANT,
        content="",
        attachments=[],
    )
    await _publish_message_event(
        event_broker,
        event_type=SessionEventType.MESSAGE_CREATED,
        session_id=session.id,
        message_id=assistant_message.id,
        role=assistant_message.role.value,
        content=assistant_message.content,
        attachments=assistant_message.attachments_json,
        created_at=assistant_message.created_at.isoformat(),
    )

    loop = asyncio.get_running_loop()
    response_future: asyncio.Future[str] = loop.create_future()
    queued_prompt = QueuedPrompt(
        content=payload.content,
        attachments=payload.attachments,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        response_future=response_future,
    )

    is_new_session_worker = await generation_manager.ensure_session(session.id)
    queued_prompt_count = await generation_manager.enqueue_prompt(session.id, queued_prompt)
    if queued_prompt_count >= 1 and not is_new_session_worker:
        await _publish_session_updated(
            event_broker,
            session,
            queued_prompt_count=queued_prompt_count,
        )

    if is_new_session_worker:
        db_engine = db_session.get_bind()
        if not isinstance(db_engine, Engine):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database engine is unavailable.",
            )
        worker_task = asyncio.create_task(
            _run_session_worker(
                db_engine=db_engine,
                session_id=session.id,
                event_broker=event_broker,
                generation_manager=generation_manager,
                chat_runtime=chat_runtime,
                runtime_service=runtime_service,
                skill_service=skill_service,
                mcp_service=mcp_service,
            )
        )
        await generation_manager.attach_worker(session.id, worker_task)

    try:
        await response_future
    except GenerationCancelledError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ChatRuntimeConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except ChatRuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    db_session.expire_all()
    refreshed_session = _get_session_or_404(repository, session_id)
    refreshed_assistant_message = repository.get_message(assistant_message.id)
    if refreshed_assistant_message is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Assistant message was not persisted.",
        )

    return ChatResponse(
        session=to_session_read(refreshed_session),
        user_message=to_message_read(user_message),
        assistant_message=to_message_read(refreshed_assistant_message),
    )
