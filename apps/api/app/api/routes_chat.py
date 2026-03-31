from __future__ import annotations

import math
from asyncio import sleep
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session as DBSession

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
    Session,
    SessionStatus,
    attachments_to_storage,
    to_message_read,
    to_session_read,
)
from app.db.repositories import SessionRepository
from app.db.session import get_db_session
from app.services.chat_runtime import (
    ChatRuntime,
    ChatRuntimeConfigurationError,
    ChatRuntimeError,
    ToolCallRequest,
    ToolCallResult,
    get_chat_runtime,
)
from app.services.runtime import (
    RuntimeArtifactPathError,
    RuntimeOperationError,
    RuntimeService,
    get_runtime_service,
)

router = APIRouter(prefix="/api/sessions", tags=["chat"])

MAX_STREAM_UPDATES = 24
STREAM_UPDATE_DELAY_SECONDS = 0.02


def _get_session_or_404(repository: SessionRepository, session_id: str) -> Session:
    session = repository.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    return session


def _build_stream_prefixes(content: str) -> list[str]:
    normalized_content = content.strip()
    if not normalized_content:
        return [""]

    step = max(1, math.ceil(len(normalized_content) / MAX_STREAM_UPDATES))
    prefixes = [normalized_content[:index] for index in range(step, len(normalized_content), step)]
    prefixes.append(normalized_content)
    return prefixes


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


async def _publish_streamed_assistant_message(
    event_broker: SessionEventBroker,
    *,
    session_id: str,
    message_id: str,
    content: str,
    attachments: list[dict[str, str | int | None]],
    created_at: str,
) -> None:
    prefixes = _build_stream_prefixes(content)

    await _publish_message_event(
        event_broker,
        event_type=SessionEventType.MESSAGE_CREATED,
        session_id=session_id,
        message_id=message_id,
        role=MessageRole.ASSISTANT.value,
        content="",
        attachments=attachments,
        created_at=created_at,
    )

    for index, prefix in enumerate(prefixes):
        await _publish_message_event(
            event_broker,
            event_type=SessionEventType.MESSAGE_UPDATED,
            session_id=session_id,
            message_id=message_id,
            role=MessageRole.ASSISTANT.value,
            content=prefix,
            attachments=attachments,
            created_at=created_at,
        )

        if index < len(prefixes) - 1:
            await sleep(STREAM_UPDATE_DELAY_SECONDS)


@router.post("/{session_id}/chat", response_model=ChatResponse)
async def create_chat_message(
    session_id: str,
    payload: ChatRequest,
    db_session: DBSession = Depends(get_db_session),
    event_broker: SessionEventBroker = Depends(get_event_broker),
    chat_runtime: ChatRuntime = Depends(get_chat_runtime),
    runtime_service: RuntimeService = Depends(get_runtime_service),
    skill_service: SkillService = Depends(get_skill_service),
) -> ChatResponse:
    repository = SessionRepository(db_session)
    session = _get_session_or_404(repository, session_id)

    session = repository.update_session(session, status=SessionStatus.RUNNING)
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_UPDATED,
            session_id=session.id,
            payload={"title": session.title, "status": session.status.value},
        )
    )

    user_message = repository.create_message(
        session=session,
        role=MessageRole.USER,
        content=payload.content,
        attachments=attachments_to_storage(payload.attachments),
    )
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.MESSAGE_CREATED,
            session_id=session.id,
            payload={
                "message_id": user_message.id,
                "role": user_message.role.value,
                "content": user_message.content,
                "attachments": user_message.attachments_json,
                "created_at": user_message.created_at.isoformat(),
            },
        )
    )

    available_skills = skill_service.list_loaded_skills_for_agent()

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
                    )
                )
            except (RuntimeArtifactPathError, RuntimeOperationError) as exc:
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

    try:
        assistant_content = await chat_runtime.generate_reply(
            payload.content,
            payload.attachments,
            available_skills=available_skills,
            execute_tool=execute_tool,
        )
    except ChatRuntimeError as exc:
        session = repository.update_session(session, status=SessionStatus.ERROR)
        await event_broker.publish(
            SessionEvent(
                type=SessionEventType.SESSION_UPDATED,
                session_id=session.id,
                payload={
                    "title": session.title,
                    "status": session.status.value,
                    "error": str(exc),
                },
            )
        )
        error_status = (
            status.HTTP_503_SERVICE_UNAVAILABLE
            if isinstance(exc, ChatRuntimeConfigurationError)
            else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(status_code=error_status, detail=str(exc)) from exc

    assistant_message = repository.create_message(
        session=session,
        role=MessageRole.ASSISTANT,
        content=assistant_content,
        attachments=[],
    )
    await _publish_streamed_assistant_message(
        event_broker,
        session_id=session.id,
        message_id=assistant_message.id,
        content=assistant_message.content,
        attachments=assistant_message.attachments_json,
        created_at=assistant_message.created_at.isoformat(),
    )

    session = repository.update_session(session, status=SessionStatus.DONE)
    await event_broker.publish(
        SessionEvent(
            type=SessionEventType.SESSION_UPDATED,
            session_id=session.id,
            payload={"title": session.title, "status": session.status.value},
        )
    )

    return ChatResponse(
        session=to_session_read(session),
        user_message=to_message_read(user_message),
        assistant_message=to_message_read(assistant_message),
    )
