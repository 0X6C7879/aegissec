from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session as DBSession
from sqlmodel import SQLModel, create_engine

from app.api.routes_sessions import _build_generation_reads
from app.db.models import (
    ExecutionStatus,
    MessageKind,
    MessageRole,
    MessageStatus,
    SessionStatus,
)
from app.db.repositories import ProjectRepository, RuntimeRepository, SessionRepository


def _build_engine(tmp_path: Path, name: str) -> Any:
    return create_engine(
        f"sqlite:///{(tmp_path / name).as_posix()}",
        connect_args={"check_same_thread": False},
    )


def test_repository_count_filters_are_consistent(tmp_path: Path) -> None:
    engine = _build_engine(tmp_path, "count-filters.db")
    SQLModel.metadata.create_all(engine)

    with DBSession(engine) as db_session:
        project_repository = ProjectRepository(db_session)
        session_repository = SessionRepository(db_session)
        runtime_repository = RuntimeRepository(db_session)

        project_alpha = project_repository.create_project(name="Alpha Project")
        project_beta = project_repository.create_project(name="Beta Project")

        session_alpha = session_repository.create_session(
            title="alpha-scan", project_id=project_alpha.id
        )
        session_beta = session_repository.create_session(
            title="beta-scan", project_id=project_beta.id
        )
        session_archived = session_repository.create_session(
            title="alpha-archive", project_id=project_beta.id
        )
        session_repository.soft_delete_session(session_archived)
        session_repository.update_session(session_beta, status=SessionStatus.DONE)

        assert session_repository.count_sessions() == 2
        assert session_repository.count_sessions(include_deleted=True) == 3
        assert session_repository.count_sessions(query="alpha", include_deleted=True) == 2
        assert session_repository.count_sessions(project_id=project_alpha.id) == 1
        assert session_repository.count_sessions(status=SessionStatus.DONE) == 1

        project_repository.soft_delete_project(project_beta)
        assert project_repository.count_projects() == 1
        assert project_repository.count_projects(include_deleted=True) == 2
        assert project_repository.count_projects(include_deleted=True, query="beta") == 1

        now = datetime.now(UTC)
        runtime_repository.create_run(
            session_id=session_alpha.id,
            command="echo alpha run",
            requested_timeout_seconds=30,
            status=ExecutionStatus.SUCCESS,
            exit_code=0,
            stdout="alpha",
            stderr="",
            container_name="runtime",
            started_at=now,
            ended_at=now,
            artifacts=[
                ("logs/alpha.txt", "C:\\tmp\\alpha.txt", "/workspace/logs/alpha.txt"),
                ("reports/summary.txt", "C:\\tmp\\summary.txt", "/workspace/reports/summary.txt"),
            ],
        )
        runtime_repository.create_run(
            session_id=session_beta.id,
            command="echo beta run",
            requested_timeout_seconds=30,
            status=ExecutionStatus.SUCCESS,
            exit_code=0,
            stdout="beta",
            stderr="",
            container_name="runtime",
            started_at=now + timedelta(seconds=1),
            ended_at=now + timedelta(seconds=1),
            artifacts=[("logs/beta.txt", "C:\\tmp\\beta.txt", "/workspace/logs/beta.txt")],
        )

        assert runtime_repository.count_runs() == 2
        assert runtime_repository.count_runs(session_id=session_alpha.id) == 1
        assert runtime_repository.count_runs(query="beta") == 1

        assert runtime_repository.count_artifacts() == 3
        assert runtime_repository.count_artifacts(session_id=session_alpha.id) == 2
        assert runtime_repository.count_artifacts(query="summary") == 1

        assert len(runtime_repository.list_runs(limit=10)) == runtime_repository.count_runs()
        assert (
            len(runtime_repository.list_artifacts(limit=10)) == runtime_repository.count_artifacts()
        )


def test_generation_step_limit_is_applied_per_generation(tmp_path: Path) -> None:
    engine = _build_engine(tmp_path, "generation-steps.db")
    SQLModel.metadata.create_all(engine)

    with DBSession(engine) as db_session:
        repository = SessionRepository(db_session)
        session = repository.create_session(title="Generation Step Session")
        branch = repository.ensure_active_branch(session)

        user_message = repository.create_message(
            session=session,
            role=MessageRole.USER,
            content="start",
            attachments=[],
            branch_id=branch.id,
            sequence=1,
            turn_index=1,
        )
        assistant_message = repository.create_message(
            session=session,
            role=MessageRole.ASSISTANT,
            content="ready",
            attachments=[],
            branch_id=branch.id,
            parent_message_id=user_message.id,
            sequence=2,
            turn_index=1,
        )

        generation_a = repository.create_generation(
            session_id=session.id,
            branch_id=branch.id,
            assistant_message_id=assistant_message.id,
            user_message_id=user_message.id,
            commit=True,
        )
        generation_b = repository.create_generation(
            session_id=session.id,
            branch_id=branch.id,
            assistant_message_id=assistant_message.id,
            user_message_id=user_message.id,
            commit=True,
        )

        base_time = datetime.now(UTC)
        for index in range(1, 4):
            repository.create_generation_step(
                generation_id=generation_a.id,
                session_id=session.id,
                message_id=assistant_message.id,
                kind="trace",
                status="done",
                sequence=index,
                started_at=base_time + timedelta(seconds=index),
            )
        for index in range(1, 3):
            repository.create_generation_step(
                generation_id=generation_b.id,
                session_id=session.id,
                message_id=assistant_message.id,
                kind="trace",
                status="done",
                sequence=index,
                started_at=base_time + timedelta(seconds=10 + index),
            )

        steps_by_generation, has_more_by_generation = repository.list_generation_steps_limited(
            generation_ids=[generation_a.id, generation_b.id],
            per_generation_limit=2,
        )
        assert [step.sequence for step in steps_by_generation[generation_a.id]] == [2, 3]
        assert [step.sequence for step in steps_by_generation[generation_b.id]] == [1, 2]
        assert has_more_by_generation[generation_a.id] is True
        assert has_more_by_generation[generation_b.id] is False

        generation_reads = _build_generation_reads(
            repository,
            session.id,
            [generation_a, generation_b],
            step_limit=2,
        )
        generation_read_by_id = {item.id: item for item in generation_reads}
        assert len(generation_read_by_id[generation_a.id].steps) == 2
        assert len(generation_read_by_id[generation_b.id].steps) == 2
        assert bool(getattr(generation_read_by_id[generation_a.id], "has_more_steps")) is True
        assert bool(getattr(generation_read_by_id[generation_b.id], "has_more_steps")) is False


def test_build_conversation_context_filters_in_sql(tmp_path: Path) -> None:
    engine = _build_engine(tmp_path, "conversation-context.db")
    SQLModel.metadata.create_all(engine)

    with DBSession(engine) as db_session:
        repository = SessionRepository(db_session)
        session = repository.create_session(title="Context Session")
        branch = repository.ensure_active_branch(session)

        repository.create_message(
            session=session,
            role=MessageRole.USER,
            content="",
            attachments=[],
            branch_id=branch.id,
            status=MessageStatus.COMPLETED,
            sequence=1,
            turn_index=1,
        )
        repository.create_message(
            session=session,
            role=MessageRole.ASSISTANT,
            content="   ",
            attachments=[],
            branch_id=branch.id,
            status=MessageStatus.COMPLETED,
            sequence=2,
            turn_index=1,
        )
        repository.create_message(
            session=session,
            role=MessageRole.ASSISTANT,
            content="assistant response",
            attachments=[],
            branch_id=branch.id,
            status=MessageStatus.STREAMING,
            sequence=3,
            turn_index=2,
        )
        repository.create_message(
            session=session,
            role=MessageRole.ASSISTANT,
            content="trace payload",
            attachments=[],
            branch_id=branch.id,
            status=MessageStatus.COMPLETED,
            message_kind=MessageKind.TRACE,
            sequence=4,
            turn_index=2,
        )
        repository.create_message(
            session=session,
            role=MessageRole.USER,
            content="cancelled",
            attachments=[],
            branch_id=branch.id,
            status=MessageStatus.CANCELLED,
            sequence=5,
            turn_index=3,
        )
        repository.create_message(
            session=session,
            role=MessageRole.ASSISTANT,
            content="assistant failed",
            attachments=[],
            branch_id=branch.id,
            status=MessageStatus.FAILED,
            sequence=6,
            turn_index=3,
        )

        context = repository.build_conversation_context(
            session_id=session.id,
            branch_id=branch.id,
            max_messages=10,
            rough_token_budget=20_000,
        )

        assert [message.sequence for message in context] == [1, 3, 6]
        assert context[0].role == MessageRole.USER
        assert context[0].content == ""
        assert all(message.message_kind == MessageKind.MESSAGE for message in context)
