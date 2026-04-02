from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "0003_chat_core_upgrade"
down_revision = "0002_project_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversation_branch",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("parent_branch_id", sa.String(), nullable=True),
        sa.Column("forked_from_message_id", sa.String(), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["forked_from_message_id"], ["message.id"]),
        sa.ForeignKeyConstraint(["parent_branch_id"], ["conversation_branch.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversation_branch_session_id", "conversation_branch", ["session_id"])

    op.create_table(
        "chat_generation",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("branch_id", sa.String(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False, server_default="reply"),
        sa.Column("user_message_id", sa.String(), nullable=True),
        sa.Column("assistant_message_id", sa.String(), nullable=False),
        sa.Column("target_message_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("reasoning_summary", sa.String(length=4000), nullable=True),
        sa.Column("reasoning_trace", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["message.id"]),
        sa.ForeignKeyConstraint(["branch_id"], ["conversation_branch.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.ForeignKeyConstraint(["target_message_id"], ["message.id"]),
        sa.ForeignKeyConstraint(["user_message_id"], ["message.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_generation_session_id", "chat_generation", ["session_id"])
    op.create_index("ix_chat_generation_branch_id", "chat_generation", ["branch_id"])
    op.create_index(
        "ix_chat_generation_assistant_message_id", "chat_generation", ["assistant_message_id"]
    )
    op.create_index("ix_chat_generation_status", "chat_generation", ["status"])
    op.create_index("ix_chat_generation_created_at", "chat_generation", ["created_at"])

    op.add_column("session", sa.Column("active_branch_id", sa.String(), nullable=True))
    op.create_index("ix_session_active_branch_id", "session", ["active_branch_id"])

    op.add_column("message", sa.Column("parent_message_id", sa.String(), nullable=True))
    op.add_column("message", sa.Column("branch_id", sa.String(), nullable=True))
    op.add_column("message", sa.Column("generation_id", sa.String(), nullable=True))
    op.add_column(
        "message",
        sa.Column("status", sa.String(length=32), nullable=True, server_default="completed"),
    )
    op.add_column(
        "message",
        sa.Column("message_kind", sa.String(length=32), nullable=True, server_default="message"),
    )
    op.add_column("message", sa.Column("sequence", sa.Integer(), nullable=True, server_default="0"))
    op.add_column(
        "message", sa.Column("turn_index", sa.Integer(), nullable=True, server_default="0")
    )
    op.add_column("message", sa.Column("edited_from_message_id", sa.String(), nullable=True))
    op.add_column("message", sa.Column("version_group_id", sa.String(), nullable=True))
    op.add_column(
        "message",
        sa.Column("metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column("message", sa.Column("error_message", sa.String(), nullable=True))
    op.add_column("message", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_message_branch_id", "message", ["branch_id"])
    op.create_index("ix_message_generation_id", "message", ["generation_id"])
    op.create_index("ix_message_status", "message", ["status"])
    op.create_index("ix_message_message_kind", "message", ["message_kind"])
    op.create_index("ix_message_sequence", "message", ["sequence"])
    op.create_index("ix_message_turn_index", "message", ["turn_index"])
    op.create_index("ix_message_version_group_id", "message", ["version_group_id"])

    connection = op.get_bind()
    now = datetime.now(UTC)

    session_table = sa.table(
        "session",
        sa.column("id", sa.String()),
        sa.column("active_branch_id", sa.String()),
    )
    branch_table = sa.table(
        "conversation_branch",
        sa.column("id", sa.String()),
        sa.column("session_id", sa.String()),
        sa.column("parent_branch_id", sa.String()),
        sa.column("forked_from_message_id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    message_table = sa.table(
        "message",
        sa.column("id", sa.String()),
        sa.column("session_id", sa.String()),
        sa.column("role", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("parent_message_id", sa.String()),
        sa.column("branch_id", sa.String()),
        sa.column("generation_id", sa.String()),
        sa.column("status", sa.String()),
        sa.column("message_kind", sa.String()),
        sa.column("sequence", sa.Integer()),
        sa.column("turn_index", sa.Integer()),
        sa.column("edited_from_message_id", sa.String()),
        sa.column("version_group_id", sa.String()),
        sa.column("metadata", sa.JSON()),
        sa.column("error_message", sa.String()),
        sa.column("completed_at", sa.DateTime(timezone=True)),
    )

    session_rows = connection.execute(sa.select(session_table.c.id)).fetchall()
    for session_row in session_rows:
        session_id = str(session_row.id)
        connection.execute(
            branch_table.insert().values(
                id=session_id,
                session_id=session_id,
                parent_branch_id=None,
                forked_from_message_id=None,
                name="Main",
                created_at=now,
                updated_at=now,
            )
        )
        connection.execute(
            session_table.update()
            .where(session_table.c.id == session_id)
            .values(active_branch_id=session_id)
        )

    message_rows = connection.execute(
        sa.select(
            message_table.c.id,
            message_table.c.session_id,
            message_table.c.role,
            message_table.c.created_at,
        ).order_by(
            message_table.c.session_id.asc(),
            message_table.c.created_at.asc(),
            message_table.c.id.asc(),
        )
    ).fetchall()

    grouped_messages: dict[str, list[sa.Row[tuple[object, ...]]]] = defaultdict(list)
    for message_row in message_rows:
        grouped_messages[str(message_row.session_id)].append(message_row)

    for session_id, rows in grouped_messages.items():
        parent_message_id: str | None = None
        turn_index = 0
        for sequence, row in enumerate(rows, start=1):
            role = str(row.role)
            if role == "user":
                turn_index += 1
            elif turn_index == 0:
                turn_index = 1
            connection.execute(
                message_table.update()
                .where(message_table.c.id == str(row.id))
                .values(
                    parent_message_id=parent_message_id,
                    branch_id=session_id,
                    generation_id=None,
                    status="completed",
                    message_kind="message",
                    sequence=sequence,
                    turn_index=turn_index,
                    edited_from_message_id=None,
                    version_group_id=str(row.id),
                    metadata={},
                    error_message=None,
                    completed_at=row.created_at,
                )
            )
            parent_message_id = str(row.id)


def downgrade() -> None:
    op.drop_index("ix_message_version_group_id", table_name="message")
    op.drop_index("ix_message_turn_index", table_name="message")
    op.drop_index("ix_message_sequence", table_name="message")
    op.drop_index("ix_message_message_kind", table_name="message")
    op.drop_index("ix_message_status", table_name="message")
    op.drop_index("ix_message_generation_id", table_name="message")
    op.drop_index("ix_message_branch_id", table_name="message")
    with op.batch_alter_table("message") as batch_op:
        batch_op.drop_column("error_message")
        batch_op.drop_column("metadata")
        batch_op.drop_column("version_group_id")
        batch_op.drop_column("edited_from_message_id")
        batch_op.drop_column("turn_index")
        batch_op.drop_column("sequence")
        batch_op.drop_column("message_kind")
        batch_op.drop_column("status")
        batch_op.drop_column("generation_id")
        batch_op.drop_column("branch_id")
        batch_op.drop_column("parent_message_id")
        batch_op.drop_column("completed_at")

    op.drop_index("ix_session_active_branch_id", table_name="session")
    with op.batch_alter_table("session") as batch_op:
        batch_op.drop_column("active_branch_id")

    op.drop_index("ix_chat_generation_created_at", table_name="chat_generation")
    op.drop_index("ix_chat_generation_status", table_name="chat_generation")
    op.drop_index("ix_chat_generation_assistant_message_id", table_name="chat_generation")
    op.drop_index("ix_chat_generation_branch_id", table_name="chat_generation")
    op.drop_index("ix_chat_generation_session_id", table_name="chat_generation")
    op.drop_table("chat_generation")

    op.drop_index("ix_conversation_branch_session_id", table_name="conversation_branch")
    op.drop_table("conversation_branch")
