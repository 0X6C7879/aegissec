from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0005_generation_step_timeline"
down_revision = "0004_chat_event_lease_upgrade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "generation_step",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("generation_id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("message_id", sa.String(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=True),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("safe_summary", sa.String(length=4000), nullable=True),
        sa.Column("delta_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("tool_name", sa.String(length=200), nullable=True),
        sa.Column("tool_call_id", sa.String(length=200), nullable=True),
        sa.Column("command", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["generation_id"], ["chat_generation.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.ForeignKeyConstraint(["message_id"], ["message.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generation_step_generation_id", "generation_step", ["generation_id"])
    op.create_index("ix_generation_step_session_id", "generation_step", ["session_id"])
    op.create_index("ix_generation_step_message_id", "generation_step", ["message_id"])
    op.create_index("ix_generation_step_sequence", "generation_step", ["sequence"])
    op.create_index("ix_generation_step_kind", "generation_step", ["kind"])
    op.create_index("ix_generation_step_phase", "generation_step", ["phase"])
    op.create_index("ix_generation_step_status", "generation_step", ["status"])
    op.create_index("ix_generation_step_state", "generation_step", ["state"])
    op.create_index("ix_generation_step_tool_name", "generation_step", ["tool_name"])
    op.create_index("ix_generation_step_tool_call_id", "generation_step", ["tool_call_id"])
    op.create_index("ix_generation_step_started_at", "generation_step", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_generation_step_started_at", table_name="generation_step")
    op.drop_index("ix_generation_step_tool_call_id", table_name="generation_step")
    op.drop_index("ix_generation_step_tool_name", table_name="generation_step")
    op.drop_index("ix_generation_step_status", table_name="generation_step")
    op.drop_index("ix_generation_step_state", table_name="generation_step")
    op.drop_index("ix_generation_step_phase", table_name="generation_step")
    op.drop_index("ix_generation_step_kind", table_name="generation_step")
    op.drop_index("ix_generation_step_sequence", table_name="generation_step")
    op.drop_index("ix_generation_step_message_id", table_name="generation_step")
    op.drop_index("ix_generation_step_session_id", table_name="generation_step")
    op.drop_index("ix_generation_step_generation_id", table_name="generation_step")
    op.drop_table("generation_step")
