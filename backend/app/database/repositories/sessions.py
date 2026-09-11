"""Database-backed agent session storage.

Lets a task survive a restart and lets two backend instances serve alternate
steps of the same task, which in-memory state cannot. Each call opens its own
short transaction, because the loop spans many HTTP round trips and holding a
session open between them would be wrong.

Failures are deliberately asymmetric:

* a failed **save** is logged and swallowed, because losing the ability to
  continue a task is better than crashing the request the user is watching;
* a failed **load** returns None, which the orchestrator already reports as
  "this task is no longer active" -- the safe direction.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete as sql_delete
from sqlalchemy import select

from app.agents.orchestrator import Session, SessionStore
from app.database.database import session_scope
from app.database.models import AgentSession

logger = logging.getLogger(__name__)


class DatabaseSessionStore(SessionStore):
    """Stores live agent state in PostgreSQL."""

    def load(self, task_id: str, user_id: str) -> Session | None:
        try:
            with session_scope() as db:
                row = db.get(AgentSession, task_id)
                if row is None:
                    return None
                # A task belongs to whoever started it. Without this, knowing a
                # task id would be enough to drive someone else's agent.
                if user_id and row.user_id and row.user_id != user_id:
                    logger.warning("Refused a cross-user session load for task %s", task_id)
                    return None
                return Session.from_dict(row.state or {})
        except Exception:  # noqa: BLE001
            logger.exception("Could not load session for task %s", task_id)
            return None

    def save(self, session: Session) -> None:
        try:
            with session_scope() as db:
                row = db.get(AgentSession, session.task_id)
                if row is None:
                    db.add(
                        AgentSession(
                            task_id=session.task_id,
                            user_id=session.user_id,
                            state=session.to_dict(),
                        )
                    )
                else:
                    row.user_id = session.user_id or row.user_id
                    row.state = session.to_dict()
        except Exception:  # noqa: BLE001
            # The task continues; it just may not survive an instance change.
            logger.exception("Could not persist session for task %s", session.task_id)

    def delete(self, task_id: str) -> None:
        try:
            with session_scope() as db:
                row = db.get(AgentSession, task_id)
                if row is not None:
                    db.delete(row)
        except Exception:  # noqa: BLE001
            logger.exception("Could not delete session for task %s", task_id)

    def purge_stale(self, older_than_seconds: int) -> int:
        """Delete sessions abandoned mid-flight.

        A user who closes the panel leaves a row behind. Nothing reaps them
        otherwise, so this is called on startup.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        try:
            with session_scope() as db:
                stale = db.scalars(
                    select(AgentSession.task_id).where(AgentSession.updated_at < cutoff)
                ).all()
                if stale:
                    db.execute(
                        sql_delete(AgentSession).where(AgentSession.task_id.in_(stale))
                    )
                return len(stale)
        except Exception:  # noqa: BLE001
            logger.exception("Could not purge stale agent sessions")
            return 0
