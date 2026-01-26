"""add_background_tasks_table

Revision ID: add_background_tasks
Revises: add_verification_tokens
Create Date: 2025-01-27 14:47:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "add_background_tasks"
down_revision: Union[str, None] = "add_verification_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "background_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_type", sa.String(length=50), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING", "RUNNING", "COMPLETED", "FAILED", name="task_status_enum"
            ),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column(
            "progress", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("parameters", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["public.users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema="public",
    )

    op.create_index(
        op.f("ix_public_background_tasks_user_id"),
        "background_tasks",
        ["user_id"],
        schema="public",
    )
    op.create_index(
        op.f("ix_public_background_tasks_task_type"),
        "background_tasks",
        ["task_type"],
        schema="public",
    )
    op.create_index(
        op.f("ix_public_background_tasks_status"),
        "background_tasks",
        ["status"],
        schema="public",
    )
    op.create_index(
        op.f("ix_public_background_tasks_created_at"),
        "background_tasks",
        ["created_at"],
        schema="public",
    )
    op.create_index(
        op.f("ix_public_background_tasks_completed_at"),
        "background_tasks",
        ["completed_at"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_public_background_tasks_completed_at"),
        table_name="background_tasks",
        schema="public",
    )
    op.drop_index(
        op.f("ix_public_background_tasks_created_at"),
        table_name="background_tasks",
        schema="public",
    )
    op.drop_index(
        op.f("ix_public_background_tasks_status"),
        table_name="background_tasks",
        schema="public",
    )
    op.drop_index(
        op.f("ix_public_background_tasks_task_type"),
        table_name="background_tasks",
        schema="public",
    )
    op.drop_index(
        op.f("ix_public_background_tasks_user_id"),
        table_name="background_tasks",
        schema="public",
    )
    op.drop_table("background_tasks", schema="public")
    op.execute("DROP TYPE task_status_enum")
