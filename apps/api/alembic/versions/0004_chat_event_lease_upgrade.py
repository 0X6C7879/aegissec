from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0004_chat_event_lease_upgrade"
down_revision = "0003_chat_core_upgrade"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_generation", sa.Column("worker_id", sa.String(), nullable=True))
    op.add_column(
        "chat_generation", sa.Column("lease_claimed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "chat_generation", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "chat_generation",
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_chat_generation_worker_id", "chat_generation", ["worker_id"])
    op.create_index("ix_chat_generation_lease_expires_at", "chat_generation", ["lease_expires_at"])

    op.create_table(
        "session_event_log",
        sa.Column("cursor", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.PrimaryKeyConstraint("cursor"),
    )
    op.create_index("ix_session_event_log_session_id", "session_event_log", ["session_id"])
    op.create_index("ix_session_event_log_event_type", "session_event_log", ["event_type"])
    op.create_index("ix_session_event_log_timestamp", "session_event_log", ["timestamp"])


def downgrade() -> None:
    op.drop_index("ix_session_event_log_timestamp", table_name="session_event_log")
    op.drop_index("ix_session_event_log_event_type", table_name="session_event_log")
    op.drop_index("ix_session_event_log_session_id", table_name="session_event_log")
    op.drop_table("session_event_log")

    op.drop_index("ix_chat_generation_lease_expires_at", table_name="chat_generation")
    op.drop_index("ix_chat_generation_worker_id", table_name="chat_generation")
    with op.batch_alter_table("chat_generation") as batch_op:
        batch_op.drop_column("attempt_count")
        batch_op.drop_column("lease_expires_at")
        batch_op.drop_column("lease_claimed_at")
        batch_op.drop_column("worker_id")
