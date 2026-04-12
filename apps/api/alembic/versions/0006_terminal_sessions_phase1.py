from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0006_terminal_sessions_phase1"
down_revision = "0005_generation_step_timeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runtime_terminal_sessions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("shell", sa.String(length=200), nullable=False),
        sa.Column("cwd", sa.String(length=1000), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runtime_terminal_sessions_session_id",
        "runtime_terminal_sessions",
        ["session_id"],
    )
    op.create_index(
        "ix_runtime_terminal_sessions_status",
        "runtime_terminal_sessions",
        ["status"],
    )
    op.create_index(
        "ix_runtime_terminal_sessions_created_at",
        "runtime_terminal_sessions",
        ["created_at"],
    )
    op.create_index(
        "ix_runtime_terminal_sessions_closed_at",
        "runtime_terminal_sessions",
        ["closed_at"],
    )

    op.create_table(
        "runtime_terminal_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("terminal_session_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.ForeignKeyConstraint(["terminal_session_id"], ["runtime_terminal_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_runtime_terminal_jobs_terminal_session_id",
        "runtime_terminal_jobs",
        ["terminal_session_id"],
    )
    op.create_index("ix_runtime_terminal_jobs_session_id", "runtime_terminal_jobs", ["session_id"])
    op.create_index("ix_runtime_terminal_jobs_status", "runtime_terminal_jobs", ["status"])
    op.create_index("ix_runtime_terminal_jobs_started_at", "runtime_terminal_jobs", ["started_at"])
    op.create_index("ix_runtime_terminal_jobs_created_at", "runtime_terminal_jobs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_runtime_terminal_jobs_created_at", table_name="runtime_terminal_jobs")
    op.drop_index("ix_runtime_terminal_jobs_started_at", table_name="runtime_terminal_jobs")
    op.drop_index("ix_runtime_terminal_jobs_status", table_name="runtime_terminal_jobs")
    op.drop_index("ix_runtime_terminal_jobs_session_id", table_name="runtime_terminal_jobs")
    op.drop_index(
        "ix_runtime_terminal_jobs_terminal_session_id",
        table_name="runtime_terminal_jobs",
    )
    op.drop_table("runtime_terminal_jobs")

    op.drop_index(
        "ix_runtime_terminal_sessions_closed_at",
        table_name="runtime_terminal_sessions",
    )
    op.drop_index(
        "ix_runtime_terminal_sessions_created_at",
        table_name="runtime_terminal_sessions",
    )
    op.drop_index("ix_runtime_terminal_sessions_status", table_name="runtime_terminal_sessions")
    op.drop_index(
        "ix_runtime_terminal_sessions_session_id",
        table_name="runtime_terminal_sessions",
    )
    op.drop_table("runtime_terminal_sessions")
