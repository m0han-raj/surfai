"""Add conversations and conversation_messages

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-11

Chat lived only in the side panel's React state, so closing the panel threw the
conversation away. The History tab showed agent tasks, which answers a
different question: a task is something SurfAI did, a conversation is something
you had.

Shaped after tasks/task_actions, cascade and indexes included, because it is
the same parent/child problem.

`page_url` records which page a turn was about so that reopening a conversation
shows where its subject changed. The page itself is never stored.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# JSONB on PostgreSQL, JSON elsewhere, matching 0001, 0002 and the models.
JSONType = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=64), nullable=False, server_default="local-user"),
        sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
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
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_index("ix_conversations_updated_at", "conversations", ["updated_at"])

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.String(length=36),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("page_url", sa.Text(), nullable=True),
        sa.Column("warnings", JSONType, nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_conversation_messages_conversation_id",
        "conversation_messages",
        ["conversation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_messages_conversation_id", "conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_index("ix_conversations_updated_at", "conversations")
    op.drop_index("ix_conversations_user_id", "conversations")
    op.drop_table("conversations")
