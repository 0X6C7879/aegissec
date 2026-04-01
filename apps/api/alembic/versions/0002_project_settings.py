from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_project_settings"
down_revision = "0001_module_a_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_settings",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("project_id", sa.String(), nullable=False),
        sa.Column("default_workflow_template", sa.String(length=120), nullable=True),
        sa.Column("default_runtime_profile_name", sa.String(length=120), nullable=True),
        sa.Column("default_queue_backend", sa.String(length=32), nullable=True),
        sa.Column("runtime_defaults", sa.JSON(), nullable=False),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["project.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id"),
    )
    op.create_index("ix_project_settings_project_id", "project_settings", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_project_settings_project_id", table_name="project_settings")
    op.drop_table("project_settings")
