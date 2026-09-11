"""Health and diagnostics."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.database.database import check_connection
from app.llm.openai_compatible import get_provider

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """Liveness plus dependency status.

    Always returns 200 so the extension can render a precise degraded state
    rather than a generic connection error.
    """
    db_ok, db_error = check_connection()
    return {
        "status": "ok" if db_ok else "degraded",
        "app": settings.app_name,
        "environment": settings.environment,
        "database": {"connected": db_ok, "error": db_error},
        "auth_provider": settings.auth_provider,
        "agent": {
            "max_steps": settings.max_agent_steps,
            "max_retries": settings.max_retries,
            "action_timeout_ms": settings.action_timeout_ms,
        },
    }


@router.get("/health/llm")
async def llm_health() -> dict:
    """Probe the configured LLM endpoint.

    Separate from `/health` because it makes a network call; the extension polls
    it only from the Settings page. The API key is never echoed back -- only
    whether one is configured.
    """
    info = await get_provider().health()
    return {"status": "ok" if info.get("reachable") else "unavailable", **info}
