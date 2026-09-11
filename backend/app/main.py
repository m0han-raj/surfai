"""FastAPI application entry point."""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.agents.page_agent import MalformedPageError
from app.api import chat, favourites, health, observe, tasks
from app.config import settings
from app.database.database import check_connection, create_all
from app.llm.openai_compatible import get_provider

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("surfai")


def _purge_stale_sessions() -> None:
    """Drop agent state left behind by tasks nobody finished.

    A user who closes the side panel mid-task leaves a row; nothing else reaps
    them. Local runs keep sessions in memory and have nothing to purge.
    """
    if settings.is_local_auth:
        return
    try:
        from app.database.repositories.sessions import DatabaseSessionStore

        removed = DatabaseSessionStore().purge_stale(settings.task_timeout_s * 4)
        if removed:
            logger.info("Purged %d stale agent session(s)", removed)
    except Exception:  # noqa: BLE001
        logger.exception("Could not purge stale agent sessions")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start up without hard dependencies.

    If PostgreSQL is not up yet the API still serves `/health`, so the extension
    can show a precise diagnosis instead of a dead connection.
    """
    connected, error = check_connection()
    if connected:
        if settings.is_serverless:
            # A managed provider's credentials never leave the platform, so a
            # fresh deployment has nowhere to run `alembic upgrade head`.
            # Bootstrap covers an empty database only, under an advisory lock,
            # and leaves an existing schema alone. Migrations remain Alembic's.
            from app.database.bootstrap import ensure_schema

            if ensure_schema():
                logger.info("Created the schema on an empty database")
        else:
            try:
                create_all()
                logger.info("Database ready")
            except Exception:
                logger.exception("Could not ensure database schema; run `alembic upgrade head`")
        _purge_stale_sessions()
    else:
        logger.warning("Database unavailable at startup (%s); /health will report degraded", error)

    logger.info("LLM endpoint: %s (model %s)", settings.llm_base_url, settings.llm_model)

    if settings.is_local_auth and settings.environment != "development":
        # Loud, because it is the difference between a personal tool and an
        # open door. See SECURITY.md.
        logger.warning(
            "AUTH_PROVIDER=local outside development: every request is treated as "
            "the same user. Do not expose this backend to a network."
        )

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


@app.exception_handler(MalformedPageError)
async def malformed_page_handler(request: Request, exc: MalformedPageError):
    """A snapshot the caller built wrong, reported as such.

    Shares a shape with the request-validation handler above because it is the
    same class of problem, just noticed a layer deeper: `page_context` is typed
    loosely at the boundary on purpose, so FastAPI never sees the error.
    """
    logger.info("Rejected a malformed page snapshot on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=422,
        content={
            "detail": f"Invalid page snapshot: {exc}",
            "code": "invalid_page_context",
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


_LANDING_PAGE = Path(__file__).resolve().parent / "static" / "index.html"


@app.get("/", include_in_schema=False)
async def root() -> Response:
    """The public face of a deployed instance.

    A bare JSON blob is a poor front door: the only people who reach this URL in
    a browser are looking for what SurfAI is and how to install it, and the
    interface itself is a Chrome extension rather than anything servable here.

    Falls back to the JSON descriptor if the file is missing, so a packaging
    mistake degrades rather than 500s.
    """
    try:
        return HTMLResponse(_LANDING_PAGE.read_text(encoding="utf-8"))
    except OSError:
        logger.warning("Landing page missing at %s; serving the JSON descriptor", _LANDING_PAGE)
        return JSONResponse(
            {
                "name": settings.app_name,
                "version": "0.1.0",
                "docs": "/docs",
                "health": "/health",
            }
        )
