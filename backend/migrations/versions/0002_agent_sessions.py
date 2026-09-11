"""Add agent_sessions for cross-instance task state

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-11

Agent state used to live in the orchestrator's memory, which meant a restart
stranded any in-flight task and a second backend instance could not serve the
next step of one it had not started.

Rows here are transient: they are deleted when a task reaches a terminal state,
and any left behind by an abandoned task are purged at startup. The foreign key
cascades, so deleting a task cannot leave orphaned state.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on PostgreSQL, JSON elsewhere, matching 0001 and the models.
JSONType = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "agent_sessions",
        sa.Column("task_id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("state", JSONType, nullable=False, server_default="{}"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_agent_sessions_user_id", "agent_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_sessions_user_id", table_name="agent_sessions")
    op.drop_table("agent_sessions")
