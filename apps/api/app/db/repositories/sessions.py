from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlmodel import Session as DBSession
from sqlmodel import col, or_, select

from app.db.models import (
    AssistantTranscriptSegment,
    ChatGeneration,
    ConversationBranch,
    GenerationAction,
    GenerationStatus,
    GenerationStep,
    Message,
    MessageKind,
    MessageRole,
    MessageStatus,
    Session,
    SessionEventLog,
    SessionStatus,
    assistant_transcript_to_storage,
    resolve_message_assistant_transcript,
    utc_now,
)


class SessionRepository:
    def __init__(self, db_session: DBSession):
        self.db_session = db_session

    def create_session(
        self,
        title: str | None = None,
        *,
        project_id: str | None = None,
        goal: str | None = None,
        scenario_type: str | None = None,
        current_phase: str | None = None,
        runtime_policy_json: dict[str, object] | None = None,
        runtime_profile_name: str | None = None,
    ) -> Session:
        session = Session(
            title=title or "New Session",
            project_id=project_id,
            goal=goal,
            scenario_type=scenario_type,
            current_phase=current_phase,
            runtime_policy_json=runtime_policy_json,
            runtime_profile_name=runtime_profile_name,
        )
        self.db_session.add(session)
        self.db_session.commit()
        self.db_session.refresh(session)

        branch = ConversationBranch(id=session.id, session_id=session.id, name="Main")
        session.active_branch_id = branch.id
        session.updated_at = utc_now()
        self.db_session.add(branch)
        self.db_session.add(session)
        self.db_session.commit()
        self.db_session.refresh(session)
        return session

    def list_sessions(
        self,
        *,
        include_deleted: bool = False,
        project_id: str | None = None,
        status: SessionStatus | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 50,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
    ) -> list[Session]:
        statement = select(Session)
        if not include_deleted:
            statement = statement.where(col(Session.deleted_at).is_(None))

        if project_id is not None:
            statement = statement.where(Session.project_id == project_id)

        if status is not None:
            statement = statement.where(Session.status == status)

        if query is not None and query.strip():
            like_query = f"%{query.strip()}%"
            statement = statement.where(
                or_(
                    col(Session.title).like(like_query),
                    col(Session.goal).like(like_query),
                    col(Session.scenario_type).like(like_query),
                    col(Session.current_phase).like(like_query),
                )
            )

        sort_column = {
            "created_at": col(Session.created_at),
            "title": col(Session.title),
            "status": col(Session.status),
        }.get(sort_by, col(Session.updated_at))
        statement = statement.order_by(
            sort_column.asc() if sort_order == "asc" else sort_column.desc(),
            col(Session.created_at).desc(),
        )
        statement = statement.offset(offset).limit(limit)
        return list(self.db_session.exec(statement).all())

    def count_sessions(
        self,
        *,
        include_deleted: bool = False,
        project_id: str | None = None,
        status: SessionStatus | None = None,
        query: str | None = None,
    ) -> int:
        return len(
            self.list_sessions(
                include_deleted=include_deleted,
                project_id=project_id,
                status=status,
                query=query,
                offset=0,
                limit=1_000_000,
            )
        )

    def get_session(self, session_id: str, *, include_deleted: bool = False) -> Session | None:
        statement = select(Session).where(Session.id == session_id)
        if not include_deleted:
            statement = statement.where(col(Session.deleted_at).is_(None))

        return self.db_session.exec(statement).first()

    def update_session(
        self,
        session: Session,
        *,
        title: str | None = None,
        status: SessionStatus | None = None,
        project_id: str | None = None,
        goal: str | None = None,
        scenario_type: str | None = None,
        current_phase: str | None = None,
        runtime_policy_json: dict[str, object] | None = None,
        runtime_profile_name: str | None = None,
        active_branch_id: str | None = None,
    ) -> Session:
        has_changes = False

        if title is not None and title != session.title:
            session.title = title
            has_changes = True

        if status is not None and status != session.status:
            session.status = status
            has_changes = True

        if project_id is not None and project_id != session.project_id:
            session.project_id = project_id
            has_changes = True

        if active_branch_id is not None and active_branch_id != session.active_branch_id:
            session.active_branch_id = active_branch_id
            has_changes = True

        if goal is not None and goal != session.goal:
            session.goal = goal
            has_changes = True

        if scenario_type is not None and scenario_type != session.scenario_type:
            session.scenario_type = scenario_type
            has_changes = True

        if current_phase is not None and current_phase != session.current_phase:
            session.current_phase = current_phase
            has_changes = True

        if runtime_policy_json is not None and runtime_policy_json != session.runtime_policy_json:
            session.runtime_policy_json = runtime_policy_json
            has_changes = True

        if (
            runtime_profile_name is not None
            and runtime_profile_name != session.runtime_profile_name
        ):
            session.runtime_profile_name = runtime_profile_name
            has_changes = True

        if has_changes:
            session.updated_at = utc_now()
            self.db_session.add(session)
            self.db_session.commit()
            self.db_session.refresh(session)

        return session

    def soft_delete_session(self, session: Session) -> Session:
        deleted_at = utc_now()
        session.deleted_at = deleted_at
        session.updated_at = deleted_at
        self.db_session.add(session)
        self.db_session.commit()
        self.db_session.refresh(session)
        return session

    def restore_session(self, session: Session) -> Session:
        session.deleted_at = None
        session.updated_at = utc_now()
        self.db_session.add(session)
        self.db_session.commit()
        self.db_session.refresh(session)
        return session

    def get_branch(self, branch_id: str) -> ConversationBranch | None:
        statement = select(ConversationBranch).where(ConversationBranch.id == branch_id)
        return self.db_session.exec(statement).first()

    def list_branches(self, session_id: str) -> list[ConversationBranch]:
        statement = (
            select(ConversationBranch)
            .where(ConversationBranch.session_id == session_id)
            .order_by(col(ConversationBranch.created_at).asc(), col(ConversationBranch.id).asc())
        )
        return list(self.db_session.exec(statement).all())

    def ensure_active_branch(self, session: Session) -> ConversationBranch:
        branch = self.get_active_branch(session)
        if branch is not None:
            return branch

        branch = ConversationBranch(id=session.id, session_id=session.id, name="Main")
        session.active_branch_id = branch.id
        session.updated_at = utc_now()
        self.db_session.add(branch)
        self.db_session.add(session)
        self.db_session.commit()
        self.db_session.refresh(branch)
        self.db_session.refresh(session)
        return branch

    def get_active_branch(self, session: Session) -> ConversationBranch | None:
        if session.active_branch_id is not None:
            branch = self.get_branch(session.active_branch_id)
            if branch is not None:
                return branch
        default_branch = self.get_branch(session.id)
        if default_branch is not None:
            return default_branch
        return None

    def create_branch(
        self,
        *,
        session: Session,
        parent_branch_id: str | None,
        forked_from_message_id: str | None,
        name: str | None,
    ) -> ConversationBranch:
        branch = ConversationBranch(
            session_id=session.id,
            parent_branch_id=parent_branch_id,
            forked_from_message_id=forked_from_message_id,
            name=name or f"Branch {len(self.list_branches(session.id)) + 1}",
        )
        self.db_session.add(branch)
        self.db_session.commit()
        self.db_session.refresh(branch)
        return branch

    def activate_branch(self, session: Session, branch: ConversationBranch) -> Session:
        session.active_branch_id = branch.id
        session.updated_at = utc_now()
        branch.updated_at = utc_now()
        self.db_session.add(branch)
        self.db_session.add(session)
        self.db_session.commit()
        self.db_session.refresh(branch)
        self.db_session.refresh(session)
        return session

    def create_message(
        self,
        *,
        session: Session,
        role: MessageRole,
        content: str,
        attachments: list[dict[str, str | int | None]],
        parent_message_id: str | None = None,
        branch_id: str | None = None,
        generation_id: str | None = None,
        status: MessageStatus = MessageStatus.COMPLETED,
        message_kind: MessageKind = MessageKind.MESSAGE,
        sequence: int = 0,
        turn_index: int = 0,
        edited_from_message_id: str | None = None,
        version_group_id: str | None = None,
        metadata_json: dict[str, object] | None = None,
        assistant_transcript_json: list[dict[str, object]] | None = None,
        error_message: str | None = None,
        commit: bool = True,
    ) -> Message:
        message = Message(
            session_id=session.id,
            parent_message_id=parent_message_id,
            branch_id=branch_id,
            generation_id=generation_id,
            role=role,
            status=status,
            message_kind=message_kind,
            sequence=sequence,
            turn_index=turn_index,
            edited_from_message_id=edited_from_message_id,
            version_group_id=version_group_id,
            content=content,
            metadata_json=dict(metadata_json or {}),
            assistant_transcript_json=list(assistant_transcript_json or []),
            error_message=error_message,
            attachments_json=list(attachments),
        )
        if message.version_group_id is None:
            message.version_group_id = message.id
        session.updated_at = utc_now()
        self.db_session.add(message)
        self.db_session.add(session)
        if commit:
            self.db_session.commit()
            self.db_session.refresh(message)
            self.db_session.refresh(session)
        return message

    def get_message(self, message_id: str) -> Message | None:
        statement = select(Message).where(Message.id == message_id)
        return self.db_session.exec(statement).first()

    def update_message(
        self,
        message: Message,
        *,
        content: str | None = None,
        status: MessageStatus | None = None,
        generation_id: str | None = None,
        metadata_json: dict[str, object] | None = None,
        assistant_transcript_json: list[dict[str, object]] | None = None,
        error_message: str | None = None,
        attachments_json: list[dict[str, str | int | None]] | None = None,
        commit: bool = True,
    ) -> Message:
        has_changes = False
        if content is not None and content != message.content:
            message.content = content
            has_changes = True
        if status is not None and status != message.status:
            message.status = status
            has_changes = True
        if generation_id is not None and generation_id != message.generation_id:
            message.generation_id = generation_id
            has_changes = True
        if metadata_json is not None and metadata_json != message.metadata_json:
            message.metadata_json = dict(metadata_json)
            has_changes = True
        if (
            assistant_transcript_json is not None
            and assistant_transcript_json != message.assistant_transcript_json
        ):
            message.assistant_transcript_json = list(assistant_transcript_json)
            has_changes = True
        if error_message is not None and error_message != message.error_message:
            message.error_message = error_message
            has_changes = True
        if attachments_json is not None and attachments_json != message.attachments_json:
            message.attachments_json = list(attachments_json)
            has_changes = True
        if not has_changes:
            return message
        self.db_session.add(message)
        if commit:
            self.db_session.commit()
            self.db_session.refresh(message)
        return message

    def append_message_trace(self, message: Message, entry: dict[str, object]) -> Message:
        metadata = dict(message.metadata_json)
        raw_trace = metadata.get("trace")
        trace_entries = list(raw_trace) if isinstance(raw_trace, list) else []
        trace_entries.append(dict(entry))
        metadata["trace"] = trace_entries
        self.update_message(message, metadata_json=metadata)
        return message

    def get_message_transcript(self, message: Message) -> list[AssistantTranscriptSegment]:
        return resolve_message_assistant_transcript(message)

    def replace_message_transcript(
        self,
        message: Message,
        segments: list[AssistantTranscriptSegment],
        *,
        commit: bool = True,
    ) -> Message:
        return self.update_message(
            message,
            assistant_transcript_json=assistant_transcript_to_storage(segments),
            commit=commit,
        )

    def append_message_transcript_segment(
        self,
        message: Message,
        segment: AssistantTranscriptSegment,
        *,
        commit: bool = True,
    ) -> Message:
        segments = self.get_message_transcript(message)
        segments.append(segment)
        return self.replace_message_transcript(message, segments, commit=commit)

    def update_message_transcript_segment(
        self,
        message: Message,
        segment: AssistantTranscriptSegment,
        *,
        commit: bool = True,
    ) -> Message:
        segments = self.get_message_transcript(message)
        updated = False
        for index, existing in enumerate(segments):
            if existing.id == segment.id:
                segments[index] = segment
                updated = True
                break
        if not updated:
            segments.append(segment)
        segments.sort(key=lambda item: (item.sequence, item.recorded_at, item.id))
        return self.replace_message_transcript(message, segments, commit=commit)

    def update_message_summary(self, message: Message, summary: str) -> Message:
        metadata = dict(message.metadata_json)
        metadata["summary"] = summary
        self.update_message(message, metadata_json=metadata)
        return message

    def list_messages(
        self,
        session_id: str,
        *,
        branch_id: str | None = None,
        include_superseded: bool = False,
    ) -> list[Message]:
        session = self.get_session(session_id, include_deleted=True)
        resolved_branch_id = branch_id or (
            session.active_branch_id if session is not None else None
        )
        statement = select(Message).where(Message.session_id == session_id)
        if resolved_branch_id is not None:
            statement = statement.where(Message.branch_id == resolved_branch_id)
        if not include_superseded:
            statement = statement.where(Message.status != MessageStatus.SUPERSEDED)
        statement = statement.order_by(col(Message.sequence).asc(), col(Message.created_at).asc())
        return list(self.db_session.exec(statement).all())

    def list_all_messages(self, session_id: str) -> list[Message]:
        statement = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(
                col(Message.branch_id).asc(),
                col(Message.sequence).asc(),
                col(Message.created_at).asc(),
            )
        )
        return list(self.db_session.exec(statement).all())

    def get_latest_visible_message(self, branch_id: str) -> Message | None:
        statement = (
            select(Message)
            .where(Message.branch_id == branch_id)
            .where(Message.status != MessageStatus.SUPERSEDED)
            .order_by(col(Message.sequence).desc(), col(Message.created_at).desc())
        )
        return self.db_session.exec(statement).first()

    def get_next_message_slot(self, branch_id: str) -> tuple[int, int]:
        latest_message = self.get_latest_visible_message(branch_id)
        if latest_message is None:
            return 1, 1
        return latest_message.sequence + 1, latest_message.turn_index + 1

    def build_conversation_context(
        self,
        *,
        session_id: str,
        branch_id: str,
        max_messages: int = 24,
        rough_token_budget: int = 12_000,
    ) -> list[Message]:
        visible_messages = self.list_messages(
            session_id, branch_id=branch_id, include_superseded=False
        )
        eligible_messages = [
            message
            for message in visible_messages
            if message.message_kind == MessageKind.MESSAGE
            and message.role in {MessageRole.USER, MessageRole.ASSISTANT}
            and message.status
            in {MessageStatus.COMPLETED, MessageStatus.STREAMING, MessageStatus.FAILED}
            and (message.role == MessageRole.USER or bool(message.content.strip()))
        ]
        truncated: list[Message] = []
        consumed_tokens = 0
        for message in reversed(eligible_messages):
            rough_tokens = max(1, len(message.content) // 4) + (len(message.attachments_json) * 16)
            if truncated and (
                len(truncated) >= max_messages
                or consumed_tokens + rough_tokens > rough_token_budget
            ):
                break
            truncated.append(message)
            consumed_tokens += rough_tokens
        truncated.reverse()
        return truncated

    def supersede_branch_descendants(
        self,
        *,
        branch_id: str,
        sequence: int,
        inclusive: bool,
        exclude_message_ids: Iterable[str] = (),
    ) -> list[Message]:
        excluded_ids = set(exclude_message_ids)
        statement = select(Message).where(Message.branch_id == branch_id)
        if inclusive:
            statement = statement.where(Message.sequence >= sequence)
        else:
            statement = statement.where(Message.sequence > sequence)
        statement = statement.where(Message.status != MessageStatus.SUPERSEDED)
        messages = list(self.db_session.exec(statement).all())
        for message in messages:
            if message.id in excluded_ids:
                continue
            message.status = MessageStatus.SUPERSEDED
            self.db_session.add(message)
        if messages:
            self.db_session.commit()
            for message in messages:
                self.db_session.refresh(message)
        return messages

    def create_generation(
        self,
        *,
        session_id: str,
        branch_id: str,
        assistant_message_id: str,
        user_message_id: str | None = None,
        action: GenerationAction = GenerationAction.REPLY,
        target_message_id: str | None = None,
        reasoning_summary: str | None = None,
        metadata_json: dict[str, object] | None = None,
        commit: bool = True,
    ) -> ChatGeneration:
        generation = ChatGeneration(
            session_id=session_id,
            branch_id=branch_id,
            action=action,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            target_message_id=target_message_id,
            reasoning_summary=reasoning_summary,
            metadata_json=dict(metadata_json or {}),
        )
        self.db_session.add(generation)
        if commit:
            self.db_session.commit()
            self.db_session.refresh(generation)
        return generation

    def get_generation_step(self, step_id: str) -> GenerationStep | None:
        statement = select(GenerationStep).where(GenerationStep.id == step_id)
        return self.db_session.exec(statement).first()

    def list_generation_steps(
        self,
        *,
        generation_id: str | None = None,
        generation_ids: Iterable[str] | None = None,
        session_id: str | None = None,
    ) -> list[GenerationStep]:
        statement = select(GenerationStep)
        if generation_id is not None:
            statement = statement.where(GenerationStep.generation_id == generation_id)
        if generation_ids is not None:
            generation_id_values = list(generation_ids)
            if not generation_id_values:
                return []
            statement = statement.where(col(GenerationStep.generation_id).in_(generation_id_values))
        if session_id is not None:
            statement = statement.where(GenerationStep.session_id == session_id)
        statement = statement.order_by(
            col(GenerationStep.generation_id).asc(),
            col(GenerationStep.sequence).asc(),
            col(GenerationStep.started_at).asc(),
        )
        return list(self.db_session.exec(statement).all())

    def get_next_generation_step_sequence(self, generation_id: str) -> int:
        steps = self.list_generation_steps(generation_id=generation_id)
        if not steps:
            return 1
        return max(step.sequence for step in steps) + 1

    def create_generation_step(
        self,
        *,
        generation_id: str,
        session_id: str,
        message_id: str | None,
        kind: str,
        phase: str | None = None,
        status: str,
        state: str | None = None,
        label: str | None = None,
        safe_summary: str | None = None,
        delta_text: str = "",
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        command: str | None = None,
        metadata_json: dict[str, object] | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        sequence: int | None = None,
        commit: bool = True,
    ) -> GenerationStep:
        step = GenerationStep(
            generation_id=generation_id,
            session_id=session_id,
            message_id=message_id,
            sequence=sequence or self.get_next_generation_step_sequence(generation_id),
            kind=kind,
            phase=phase,
            status=status,
            state=state,
            label=label,
            safe_summary=safe_summary,
            delta_text=delta_text,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            command=command,
            started_at=started_at or utc_now(),
            ended_at=ended_at,
            metadata_json=dict(metadata_json or {}),
        )
        self.db_session.add(step)
        if commit:
            self.db_session.commit()
            self.db_session.refresh(step)
        return step

    def update_generation_step(
        self,
        step: GenerationStep,
        *,
        phase: str | None = None,
        status: str | None = None,
        state: str | None = None,
        label: str | None = None,
        safe_summary: str | None = None,
        delta_text: str | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
        command: str | None = None,
        metadata_json: dict[str, object] | None = None,
        ended_at: datetime | None = None,
        commit: bool = True,
    ) -> GenerationStep:
        if phase is not None:
            step.phase = phase
        if status is not None:
            step.status = status
        if state is not None:
            step.state = state
        if label is not None:
            step.label = label
        if safe_summary is not None:
            step.safe_summary = safe_summary
        if delta_text is not None:
            step.delta_text = delta_text
        if tool_name is not None:
            step.tool_name = tool_name
        if tool_call_id is not None:
            step.tool_call_id = tool_call_id
        if command is not None:
            step.command = command
        if metadata_json is not None:
            step.metadata_json = dict(metadata_json)
        if ended_at is not None:
            step.ended_at = ended_at
        self.db_session.add(step)
        if commit:
            self.db_session.commit()
            self.db_session.refresh(step)
        return step

    def append_generation_step_delta(
        self,
        step: GenerationStep,
        delta_text: str,
        *,
        commit: bool = True,
    ) -> GenerationStep:
        step.delta_text = f"{step.delta_text}{delta_text}"
        self.db_session.add(step)
        if commit:
            self.db_session.commit()
            self.db_session.refresh(step)
        return step

    def get_open_generation_step(
        self,
        generation_id: str,
        *,
        kind: str | None = None,
        tool_call_id: str | None = None,
    ) -> GenerationStep | None:
        statement = select(GenerationStep).where(GenerationStep.generation_id == generation_id)
        statement = statement.where(col(GenerationStep.ended_at).is_(None))
        if kind is not None:
            statement = statement.where(GenerationStep.kind == kind)
        if tool_call_id is not None:
            statement = statement.where(GenerationStep.tool_call_id == tool_call_id)
        statement = statement.order_by(col(GenerationStep.sequence).desc())
        return self.db_session.exec(statement).first()

    def close_open_generation_steps(
        self,
        generation_id: str,
        *,
        status: str,
        state: str | None = None,
        commit: bool = True,
    ) -> list[GenerationStep]:
        statement = (
            select(GenerationStep)
            .where(GenerationStep.generation_id == generation_id)
            .where(col(GenerationStep.ended_at).is_(None))
            .order_by(col(GenerationStep.sequence).asc())
        )
        steps = list(self.db_session.exec(statement).all())
        now = utc_now()
        for step in steps:
            step.status = status
            if state is not None:
                step.state = state
            step.ended_at = now
            self.db_session.add(step)
        if steps and commit:
            self.db_session.commit()
            for step in steps:
                self.db_session.refresh(step)
        return steps

    def create_session_event(
        self,
        *,
        session_id: str,
        event_type: str,
        payload: dict[str, object],
        timestamp: datetime | None = None,
    ) -> SessionEventLog:
        event = SessionEventLog(
            session_id=session_id,
            event_type=event_type,
            payload_json=dict(payload),
            timestamp=timestamp or utc_now(),
        )
        self.db_session.add(event)
        self.db_session.commit()
        self.db_session.refresh(event)
        return event

    def list_session_events(
        self,
        session_id: str,
        *,
        after_cursor: int | None = None,
        limit: int = 2_000,
    ) -> list[SessionEventLog]:
        statement = select(SessionEventLog).where(SessionEventLog.session_id == session_id)
        if after_cursor is not None:
            statement = statement.where(col(SessionEventLog.cursor) > after_cursor)
        statement = statement.order_by(col(SessionEventLog.cursor).asc()).limit(limit)
        return list(self.db_session.exec(statement).all())

    def get_generation(self, generation_id: str) -> ChatGeneration | None:
        statement = select(ChatGeneration).where(ChatGeneration.id == generation_id)
        return self.db_session.exec(statement).first()

    def list_generations(
        self,
        session_id: str,
        *,
        statuses: set[GenerationStatus] | None = None,
    ) -> list[ChatGeneration]:
        statement = select(ChatGeneration).where(ChatGeneration.session_id == session_id)
        if statuses:
            statement = statement.where(col(ChatGeneration.status).in_(statuses))
        statement = statement.order_by(
            col(ChatGeneration.created_at).asc(), col(ChatGeneration.id).asc()
        )
        return list(self.db_session.exec(statement).all())

    def get_active_generation(self, session_id: str) -> ChatGeneration | None:
        statement = (
            select(ChatGeneration)
            .where(ChatGeneration.session_id == session_id)
            .where(ChatGeneration.status == GenerationStatus.RUNNING)
            .order_by(col(ChatGeneration.created_at).asc())
        )
        return self.db_session.exec(statement).first()

    def claim_next_generation(
        self,
        session_id: str,
        *,
        worker_id: str,
        lease_seconds: int = 300,
    ) -> ChatGeneration | None:
        statement = (
            select(ChatGeneration)
            .where(ChatGeneration.session_id == session_id)
            .where(ChatGeneration.status == GenerationStatus.QUEUED)
            .order_by(col(ChatGeneration.created_at).asc(), col(ChatGeneration.id).asc())
        )
        generation = self.db_session.exec(statement).first()
        if generation is None:
            return None
        now = utc_now()
        generation.status = GenerationStatus.RUNNING
        generation.updated_at = now
        generation.started_at = now
        generation.worker_id = worker_id
        generation.lease_claimed_at = now
        generation.lease_expires_at = now
        generation.lease_expires_at = generation.lease_expires_at.replace(
            microsecond=0
        ) + timedelta(seconds=lease_seconds)
        generation.attempt_count = (generation.attempt_count or 0) + 1
        self.db_session.add(generation)
        self.db_session.commit()
        self.db_session.refresh(generation)
        return generation

    def update_generation(
        self,
        generation: ChatGeneration,
        *,
        status: GenerationStatus | None = None,
        error_message: str | None = None,
        reasoning_summary: str | None = None,
        reasoning_trace_json: list[dict[str, object]] | None = None,
        metadata_json: dict[str, object] | None = None,
        cancel_requested_at: datetime | None = None,
        worker_id: str | None = None,
        lease_claimed_at: datetime | None = None,
        lease_expires_at: datetime | None = None,
        commit: bool = True,
    ) -> ChatGeneration:
        if status is not None:
            generation.status = status
            generation.updated_at = utc_now()
            if status in {
                GenerationStatus.COMPLETED,
                GenerationStatus.FAILED,
                GenerationStatus.CANCELLED,
            }:
                generation.ended_at = utc_now()
                generation.worker_id = None
                generation.lease_claimed_at = None
                generation.lease_expires_at = None
        if error_message is not None:
            generation.error_message = error_message
        if reasoning_summary is not None:
            generation.reasoning_summary = reasoning_summary
        if reasoning_trace_json is not None:
            generation.reasoning_trace_json = list(reasoning_trace_json)
        if metadata_json is not None:
            generation.metadata_json = dict(metadata_json)
        if cancel_requested_at is not None:
            generation.cancel_requested_at = cancel_requested_at
        if worker_id is not None:
            generation.worker_id = worker_id
        if lease_claimed_at is not None:
            generation.lease_claimed_at = lease_claimed_at
        if lease_expires_at is not None:
            generation.lease_expires_at = lease_expires_at
        self.db_session.add(generation)
        if commit:
            self.db_session.commit()
            self.db_session.refresh(generation)
        return generation

    def recover_abandoned_generations(self, *, now: datetime | None = None) -> int:
        current_time = now or utc_now()
        statement = (
            select(ChatGeneration)
            .where(ChatGeneration.status == GenerationStatus.RUNNING)
            .where(
                or_(
                    col(ChatGeneration.lease_expires_at).is_(None),
                    col(ChatGeneration.lease_expires_at) < current_time,
                )
            )
            .order_by(col(ChatGeneration.created_at).asc(), col(ChatGeneration.id).asc())
        )
        abandoned = list(self.db_session.exec(statement).all())
        for generation in abandoned:
            generation.status = GenerationStatus.QUEUED
            generation.updated_at = current_time
            generation.worker_id = None
            generation.lease_claimed_at = None
            generation.lease_expires_at = None
            generation.error_message = None
            self.db_session.add(generation)
        if abandoned:
            self.db_session.commit()
            for generation in abandoned:
                self.db_session.refresh(generation)
        return len(abandoned)

    def mark_generation_completed(self, generation: ChatGeneration) -> ChatGeneration:
        return self.update_generation(generation, status=GenerationStatus.COMPLETED)

    def mark_generation_failed(
        self, generation: ChatGeneration, error_message: str
    ) -> ChatGeneration:
        return self.update_generation(
            generation,
            status=GenerationStatus.FAILED,
            error_message=error_message,
        )

    def cancel_generation(
        self, generation: ChatGeneration, *, error_message: str
    ) -> ChatGeneration:
        return self.update_generation(
            generation,
            status=GenerationStatus.CANCELLED,
            error_message=error_message,
            cancel_requested_at=utc_now(),
        )

    def cancel_queued_generations(
        self, session_id: str, *, error_message: str
    ) -> list[ChatGeneration]:
        generations = self.list_generations(session_id, statuses={GenerationStatus.QUEUED})
        for generation in generations:
            generation.status = GenerationStatus.CANCELLED
            generation.error_message = error_message
            generation.updated_at = utc_now()
            generation.ended_at = utc_now()
            self.db_session.add(generation)
        if generations:
            self.db_session.commit()
            for generation in generations:
                self.db_session.refresh(generation)
        return generations

    def queue_size(self, session_id: str) -> int:
        return len(self.list_generations(session_id, statuses={GenerationStatus.QUEUED}))

    def get_generation_queue_position(self, session_id: str, generation_id: str) -> int | None:
        queued_generations = self.list_generations(session_id, statuses={GenerationStatus.QUEUED})
        for index, generation in enumerate(queued_generations, start=1):
            if generation.id == generation_id:
                return index
        return None

    def clone_branch_path_to_message(
        self,
        *,
        session: Session,
        source_branch_id: str,
        target_message: Message,
        new_branch: ConversationBranch,
    ) -> list[Message]:
        source_messages = self.list_messages(
            session.id, branch_id=source_branch_id, include_superseded=False
        )
        cloned_messages: list[Message] = []
        id_map: dict[str, str] = {}
        for source_message in source_messages:
            if source_message.sequence > target_message.sequence:
                break
            cloned_message = self.create_message(
                session=session,
                role=source_message.role,
                content=source_message.content,
                attachments=source_message.attachments_json,
                parent_message_id=id_map.get(source_message.parent_message_id or ""),
                branch_id=new_branch.id,
                generation_id=None,
                status=source_message.status,
                message_kind=source_message.message_kind,
                sequence=source_message.sequence,
                turn_index=source_message.turn_index,
                edited_from_message_id=None,
                version_group_id=source_message.version_group_id,
                metadata_json=source_message.metadata_json,
                error_message=source_message.error_message,
                commit=False,
            )
            cloned_messages.append(cloned_message)
            id_map[source_message.id] = cloned_message.id
            self.db_session.add(cloned_message)
        self.db_session.commit()
        for message in cloned_messages:
            self.db_session.refresh(message)
        return cloned_messages
