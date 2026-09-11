"""Shared FastAPI dependencies."""

from __future__ import annotations

from app.agents.orchestrator import MemorySessionStore, Orchestrator, SessionStore
from app.config import settings
from app.database.repositories.sessions import DatabaseSessionStore
from app.database.repositories.tasks import DatabaseTaskStore
from app.llm.openai_compatible import get_provider

_orchestrator: Orchestrator | None = None
_session_store: SessionStore | None = None


def get_session_store() -> SessionStore:
    """Where in-flight agent state lives.

    Local single-user runs keep it in memory, which is faster and leaves no
    rows behind. Anything else has to survive a restart and a second instance,
    so it goes to the database.
    """
    global _session_store
    if _session_store is None:
        _session_store = (
            MemorySessionStore() if settings.is_local_auth else DatabaseSessionStore()
        )
    return _session_store


def set_session_store(store: SessionStore | None) -> None:
    """Override the store. Used by tests."""
    global _session_store
    _session_store = store


def get_orchestrator() -> Orchestrator:
    """Process-wide orchestrator.

    It holds live session state across the HTTP round trips that drive one task,
    so it must be a singleton rather than per-request.
    """
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator(
            get_provider(), DatabaseTaskStore(), get_session_store()
        )
    return _orchestrator


def set_orchestrator(orchestrator: Orchestrator | None) -> None:
    """Override the singleton. Used by tests."""
    global _orchestrator
    _orchestrator = orchestrator
