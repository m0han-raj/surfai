"""FastAPI application entry point."""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import chat, favourites, health, observe, tasks
from app.config import settings
from app.database.database import check_connection, create_all
from app.llm.openai_compatible import get_provider

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("surfai")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start up without hard dependencies.

    If PostgreSQL is not up yet the API still serves `/health`, so the extension
    can show a precise diagnosis instead of a dead connection.
    """
    connected, error = check_connection()
    if connected:
        try:
            create_all()
            logger.info("Database ready")
        except Exception:
            logger.exception("Could not ensure database schema; run `alembic upgrade head`")
    else:
        logger.warning("Database unavailable at startup (%s); /health will report degraded", error)

    logger.info("LLM endpoint: %s (model %s)", settings.llm_base_url, settings.llm_model)
    yield
    await get_provider().aclose()


app = FastAPI(
    title="SurfAI Backend",
    version="0.1.0",
    description=(
        "Agent orchestration, AI-aware memory and safety enforcement for the SurfAI "
        "Chrome extension."
    ),
    lifespan=lifespan,
)


class ExtensionCORSMiddleware(CORSMiddleware):
    """CORS with support for a `chrome-extension://*` wildcard.

    An unpacked extension's id changes between machines, so pinning one origin
    would break every fresh checkout. The wildcard is restricted to the
    chrome-extension scheme -- it does not allow arbitrary web origins.
    """

    def __init__(self, app, allow_origins: list[str], **kwargs) -> None:  # noqa: ANN001
        self._patterns = [
            re.compile("^" + re.escape(o).replace(r"\*", ".*") + "$")
            for o in allow_origins
            if "*" in o
        ]
        exact = [o for o in allow_origins if "*" not in o]
        super().__init__(app, allow_origins=exact, **kwargs)

    def is_allowed_origin(self, origin: str) -> bool:
        if super().is_allowed_origin(origin):
            return True
        return any(p.match(origin) for p in self._patterns)


app.add_middleware(
    ExtensionCORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    """Report malformed requests without leaking internals."""
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", ())[1:]) or "request"
    return JSONResponse(
        status_code=422,
        content={
            "detail": f"Invalid request: {field} - {first.get('msg', 'validation failed')}",
            "code": "invalid_request",
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    """Never surface a stack trace to the user.

    The full traceback goes to the server log; the client gets a stable,
    actionable sentence.
    """
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": (
                "SurfAI hit an unexpected error handling that request. "
                "The details were written to the backend log."
            ),
            "code": "internal_error",
        },
    )


app.include_router(health.router)
app.include_router(chat.router)
app.include_router(tasks.router)
app.include_router(favourites.router)
app.include_router(observe.router)


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "name": settings.app_name,
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
    }
