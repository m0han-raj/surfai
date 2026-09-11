"""SQLAlchemy models.

`preferences` and `metadata` use JSONB on PostgreSQL and fall back to generic
JSON on SQLite so the same models power the test-suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

# JSONB on PostgreSQL, JSON elsewhere. Indexable and queryable on PG.
JSONType = JSON().with_variant(JSONB(), "postgresql")


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Favourite(Base):
    """An AI-aware favourite: a URL plus the *intent* behind visiting it."""

    __tablename__ = "favourites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local-user")
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    intent: Mapped[str] = mapped_column(Text, nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    preferences: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    # `metadata` is reserved by the Declarative API, so the attribute is
    # `meta` while the column keeps the specified name.
    meta: Mapped[dict] = mapped_column("metadata", JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=_now,
        onupdate=_now,
        server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_favourites_user_id", "user_id"),
        Index("ix_favourites_domain", "domain"),
    )


class Task(Base):
    """One user request and its lifecycle."""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local-user")
    request: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="IDLE")
    current_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    favourite_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("favourites.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    actions: Mapped[list[TaskAction]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskAction.step_number",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_tasks_user_id", "user_id"),
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_created_at", "created_at"),
    )


class TaskAction(Base):
    """A single step within a task, with its structured result."""

    __tablename__ = "task_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[str | None] = mapped_column(String(64), nullable=True)
    arguments: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, server_default=func.now()
    )

    task: Mapped[Task] = relationship(back_populates="actions")

    __table_args__ = (Index("ix_task_actions_task_id", "task_id"),)
