from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001_module_a_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "session",
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("goal", sa.String(length=4000), nullable=True),
        sa.Column("scenario_type", sa.String(length=200), nullable=True),
        sa.Column("current_phase", sa.String(length=200), nullable=True),
        sa.Column("runtime_policy_json", sa.JSON(), nullable=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_session_project_id", "session", ["project_id"])

    op.create_table(
        "message",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("attachments", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_message_session_id", "message", ["session_id"])

    op.create_table(
        "runtime_execution_run",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("command", sa.String(), nullable=False),
        sa.Column("requested_timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("stdout", sa.String(), nullable=False),
        sa.Column("stderr", sa.String(), nullable=False),
        sa.Column("container_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runtime_execution_run_session_id", "runtime_execution_run", ["session_id"])

    op.create_table(
        "runtime_artifact",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("relative_path", sa.String(), nullable=False),
        sa.Column("host_path", sa.String(), nullable=False),
        sa.Column("container_path", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runtime_execution_run.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runtime_artifact_run_id", "runtime_artifact", ["run_id"])

    op.create_table(
        "run_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=True),
        sa.Column("project_id", sa.String(), nullable=True),
        sa.Column("run_id", sa.String(), nullable=True),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["runtime_execution_run.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_log_session_id", "run_log", ["session_id"])
    op.create_index("ix_run_log_project_id", "run_log", ["project_id"])
    op.create_index("ix_run_log_run_id", "run_log", ["run_id"])

    op.create_table(
        "skill_record",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("root_dir", sa.String(), nullable=False),
        sa.Column("directory_name", sa.String(), nullable=False),
        sa.Column("entry_file", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("compatibility", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("raw_frontmatter", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=False),
        sa.Column("last_scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_file"),
    )

    op.create_table(
        "mcp_server",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("transport", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("command", sa.String(), nullable=True),
        sa.Column("args", sa.JSON(), nullable=False),
        sa.Column("env", sa.JSON(), nullable=False),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("headers", sa.JSON(), nullable=False),
        sa.Column("timeout_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("config_path", sa.String(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "mcp_capability",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("server_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("uri", sa.String(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["server_id"], ["mcp_server.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "workflow_run",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("template_name", sa.String(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_stage", sa.String(), nullable=True),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "task_node",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workflow_run_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("node_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["task_node.id"]),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_run.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "graph_node",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("workflow_run_id", sa.String(), nullable=False),
        sa.Column("graph_type", sa.String(length=32), nullable=False),
        sa.Column("node_type", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("stable_key", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_run.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "graph_edge",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("workflow_run_id", sa.String(), nullable=False),
        sa.Column("graph_type", sa.String(length=32), nullable=False),
        sa.Column("source_node_id", sa.String(), nullable=False),
        sa.Column("target_node_id", sa.String(), nullable=False),
        sa.Column("relation", sa.String(), nullable=False),
        sa.Column("stable_key", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["session.id"]),
        sa.ForeignKeyConstraint(["source_node_id"], ["graph_node.id"]),
        sa.ForeignKeyConstraint(["target_node_id"], ["graph_node.id"]),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_run.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("graph_edge")
    op.drop_table("graph_node")
    op.drop_table("task_node")
    op.drop_table("workflow_run")
    op.drop_table("mcp_capability")
    op.drop_table("mcp_server")
    op.drop_table("skill_record")
    op.drop_index("ix_run_log_run_id", table_name="run_log")
    op.drop_index("ix_run_log_project_id", table_name="run_log")
    op.drop_index("ix_run_log_session_id", table_name="run_log")
    op.drop_table("run_log")
    op.drop_index("ix_runtime_artifact_run_id", table_name="runtime_artifact")
    op.drop_table("runtime_artifact")
    op.drop_index("ix_runtime_execution_run_session_id", table_name="runtime_execution_run")
    op.drop_table("runtime_execution_run")
    op.drop_index("ix_message_session_id", table_name="message")
    op.drop_table("message")
    op.drop_index("ix_session_project_id", table_name="session")
    op.drop_table("session")
    op.drop_table("project")
