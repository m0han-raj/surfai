"""Chat history persistence.

Every method is scoped to one user, and scoped by construction rather than by
the caller remembering: a conversation is only ever reached through a query
that already carries the owner. An id is guessable, so an unscoped lookup would
be enough to read or append to somebody else's thread.

Conversations are written as a side effect of answering, in the same place
tasks are recorded, which is why the panel closing mid-answer does not lose the
turn. That also means a failure here must never surface to the user: the caller
catches, and the answer goes out regardless.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from app.database.database import session_scope
from app.database.models import Conversation, ConversationMessage

TITLE_LIMIT = 200


def _now() -> datetime:
    return datetime.now(UTC)


def _message_to_dict(message: ConversationMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "page_url": message.page_url,
        "warnings": message.warnings or [],
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


class ConversationRepository:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id

    # -- reading ----------------------------------------------------------

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        """Recent conversations, newest activity first, without their messages.

        A summary rather than the threads themselves: opening the History tab
        should not fetch every message the user has ever exchanged.
        """
        with session_scope() as session:
            counts = (
                select(
                    ConversationMessage.conversation_id.label("conversation_id"),
                    func.count(ConversationMessage.id).label("message_count"),
                )
                .group_by(ConversationMessage.conversation_id)
                .subquery()
            )
            rows = session.execute(
                select(Conversation, func.coalesce(counts.c.message_count, 0))
                .outerjoin(counts, counts.c.conversation_id == Conversation.id)
                .where(Conversation.user_id == self.user_id)
                .order_by(Conversation.updated_at.desc())
                .limit(limit)
            ).all()

            return [
                {
                    "id": conversation.id,
                    "title": conversation.title,
                    "message_count": int(count),
                    "created_at": conversation.created_at.isoformat()
                    if conversation.created_at
                    else None,
                    "updated_at": conversation.updated_at.isoformat()
                    if conversation.updated_at
                    else None,
                }
                for conversation, count in rows
            ]

    def get(self, conversation_id: str) -> dict[str, Any] | None:
        """One conversation with its transcript, or None if it is not theirs."""
        with session_scope() as session:
            conversation = self._owned(session, conversation_id)
            if conversation is None:
                return None
            return {
                "id": conversation.id,
                "title": conversation.title,
                "created_at": conversation.created_at.isoformat()
                if conversation.created_at
                else None,
                "updated_at": conversation.updated_at.isoformat()
                if conversation.updated_at
                else None,
                "messages": [_message_to_dict(m) for m in conversation.messages],
            }

    # -- writing ----------------------------------------------------------

    def record(
        self,
        *,
        conversation_id: str | None,
        user_message: str,
        reply: str,
        page_url: str | None = None,
        warnings: list[str] | None = None,
    ) -> str:
        """Append one exchange. Returns the conversation it landed in.

        An unknown or someone else's `conversation_id` starts a new
        conversation rather than failing or, worse, appending to a thread that
        is not theirs.
        """
        with session_scope() as session:
            conversation = (
                self._owned(session, conversation_id) if conversation_id else None
            )
            if conversation is None:
                conversation = Conversation(
                    user_id=self.user_id,
                    title=(user_message or "").strip()[:TITLE_LIMIT],
                )
                session.add(conversation)
                session.flush()

            now = _now()
            session.add(
                ConversationMessage(
                    conversation_id=conversation.id,
                    role="user",
                    content=user_message,
                    page_url=page_url,
                    warnings=[],
                    created_at=now,
                )
            )
            session.add(
                ConversationMessage(
                    conversation_id=conversation.id,
                    role="assistant",
                    content=reply,
                    page_url=page_url,
                    warnings=list(warnings or []),
                    # A shade later, so the pair always reads back in the order
                    # it happened even when both land in the same instant.
                    created_at=now.replace(microsecond=min(now.microsecond + 1, 999_999)),
                )
            )
            conversation.updated_at = now
            return conversation.id

    def note(self, conversation_id: str, content: str, *, page_url: str | None = None) -> None:
        """Record SurfAI remarking that the page underneath the thread changed."""
        with session_scope() as session:
            conversation = self._owned(session, conversation_id)
            if conversation is None:
                return
            session.add(
                ConversationMessage(
                    conversation_id=conversation.id,
                    role="notice",
                    content=content,
                    page_url=page_url,
                    warnings=[],
                    created_at=_now(),
                )
            )

    def delete(self, conversation_id: str) -> bool:
        """Remove a conversation and its messages. False if it was not theirs."""
        with session_scope() as session:
            conversation = self._owned(session, conversation_id)
            if conversation is None:
                return False
            session.delete(conversation)
            return True

    # -- internals --------------------------------------------------------

    def _owned(self, session, conversation_id: str | None) -> Conversation | None:  # noqa: ANN001
        """The conversation, only if this user owns it.

        The single place ownership is decided, so no caller can forget it.
        """
        if not conversation_id:
            return None
        return session.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.user_id == self.user_id,
            )
        ).scalar_one_or_none()
