"""Initial schema: favourites, tasks, task_actions

Revision ID: 0001
Revises:
Create Date: 2026-09-11

JSONB is used on PostgreSQL for `preferences`/`metadata`/`arguments`/`result`
and degrades to JSON on other dialects, so the same migration runs against the
SQLite fallback used in development and tests.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONType = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "favourites",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False, server_default="local-user"),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("intent", sa.Text(), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("preferences", JSONType, nullable=False, server_default="{}"),
        sa.Column("metadata", JSONType, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_favourites_user_id", "favourites", ["user_id"])
    op.create_index("ix_favourites_domain", "favourites", ["domain"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False, server_default="local-user"),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="IDLE"),
        sa.Column("current_url", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "favourite_id",
            sa.String(length=36),
            sa.ForeignKey("favourites.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_created_at", "tasks", ["created_at"])

    op.create_table(
        "task_actions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(length=36),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(length=32), nullable=False),
        sa.Column("target", sa.String(length=64), nullable=True),
        sa.Column("arguments", JSONType, nullable=False, server_default="{}"),
        sa.Column("result", JSONType, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_task_actions_task_id", "task_actions", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_task_actions_task_id", table_name="task_actions")
    op.drop_table("task_actions")
    op.drop_index("ix_tasks_created_at", table_name="tasks")
    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_user_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_favourites_domain", table_name="favourites")
    op.drop_index("ix_favourites_user_id", table_name="favourites")
    op.drop_table("favourites")
