"""Shared FastAPI dependencies."""

from __future__ import annotations

from app.agents.orchestrator import Orchestrator
from app.database.repositories.tasks import DatabaseTaskStore
from app.llm.openai_compatible import get_provider

_orchestrator: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    """Process-wide orchestrator.

    It holds live session state across the HTTP round trips that drive one task,
    so it must be a singleton rather than per-request.
    """
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator(get_provider(), DatabaseTaskStore())
    return _orchestrator


def set_orchestrator(orchestrator: Orchestrator | None) -> None:
    """Override the singleton. Used by tests."""
    global _orchestrator
    _orchestrator = orchestrator
