from __future__ import annotations

from alembic import op

revision = "0007_performance_composite_indexes"
down_revision = "0006_terminal_sessions_phase1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_message_session_branch_status_sequence",
        "message",
        ["session_id", "branch_id", "status", "sequence"],
        unique=False,
    )
    op.create_index(
        "ix_message_session_branch_created_id",
        "message",
        ["session_id", "branch_id", "created_at", "id"],
        unique=False,
    )

    op.create_index(
        "ix_chat_generation_session_status_created",
        "chat_generation",
        ["session_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_chat_generation_session_branch_created",
        "chat_generation",
        ["session_id", "branch_id", "created_at"],
        unique=False,
    )

    op.create_index(
        "ix_generation_step_generation_sequence",
        "generation_step",
        ["generation_id", "sequence"],
        unique=False,
    )
    op.create_index(
        "ix_generation_step_session_generation_started",
        "generation_step",
        ["session_id", "generation_id", "started_at"],
        unique=False,
    )

    op.create_index(
        "ix_session_event_log_session_cursor",
        "session_event_log",
        ["session_id", "cursor"],
        unique=False,
    )
    op.create_index(
        "ix_session_event_log_session_timestamp",
        "session_event_log",
        ["session_id", "timestamp"],
        unique=False,
    )

    op.create_index(
        "ix_runtime_execution_run_session_started",
        "runtime_execution_run",
        ["session_id", "started_at"],
        unique=False,
    )
    op.create_index(
        "ix_runtime_execution_run_started_id",
        "runtime_execution_run",
        ["started_at", "id"],
        unique=False,
    )

    op.create_index(
        "ix_runtime_artifact_run_created",
        "runtime_artifact",
        ["run_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_runtime_artifact_created_id",
        "runtime_artifact",
        ["created_at", "id"],
        unique=False,
    )

    op.create_index(
        "ix_graph_node_session_workflow_type_stable",
        "graph_node",
        ["session_id", "workflow_run_id", "graph_type", "stable_key"],
        unique=False,
    )
    op.create_index(
        "ix_graph_edge_session_workflow_type_stable",
        "graph_edge",
        ["session_id", "workflow_run_id", "graph_type", "stable_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_graph_edge_session_workflow_type_stable", table_name="graph_edge")
    op.drop_index("ix_graph_node_session_workflow_type_stable", table_name="graph_node")

    op.drop_index("ix_runtime_artifact_created_id", table_name="runtime_artifact")
    op.drop_index("ix_runtime_artifact_run_created", table_name="runtime_artifact")

    op.drop_index("ix_runtime_execution_run_started_id", table_name="runtime_execution_run")
    op.drop_index("ix_runtime_execution_run_session_started", table_name="runtime_execution_run")

    op.drop_index("ix_session_event_log_session_timestamp", table_name="session_event_log")
    op.drop_index("ix_session_event_log_session_cursor", table_name="session_event_log")

    op.drop_index("ix_generation_step_session_generation_started", table_name="generation_step")
    op.drop_index("ix_generation_step_generation_sequence", table_name="generation_step")

    op.drop_index("ix_chat_generation_session_branch_created", table_name="chat_generation")
    op.drop_index("ix_chat_generation_session_status_created", table_name="chat_generation")

    op.drop_index("ix_message_session_branch_created_id", table_name="message")
    op.drop_index("ix_message_session_branch_status_sequence", table_name="message")
