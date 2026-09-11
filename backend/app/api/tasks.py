"""Task lifecycle: create, step, cancel, and read history."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.agents.orchestrator import TERMINAL_STATES, Orchestrator
from app.api.deps import get_orchestrator
from app.api.schemas import ContinueRequest, DirectiveResponse, TaskCreate
from app.database.database import get_db
from app.database.repositories.favourites import FavouriteRepository, to_dict
from app.database.repositories.tasks import TaskRepository, task_to_dict
from app.security.permissions import User, get_current_user

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("", response_model=DirectiveResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreate,
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict:
    """Start a task and return the first directive."""
    favourite = None
    if payload.favourite_id:
        record = FavouriteRepository(session, user.id).get(payload.favourite_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Favourite not found")
        favourite = to_dict(record)

    page = _merge_page(payload.page_context, payload.tab_context)
    directive = await orchestrator.start(
        task_id=str(uuid.uuid4()),
        message=payload.request,
        page=page,
        favourite=favourite,
    )
    return directive.to_dict()


@router.post("/{task_id}/continue", response_model=DirectiveResponse)
async def continue_task(
    task_id: str,
    payload: ContinueRequest,
    orchestrator: Orchestrator = Depends(get_orchestrator),
    user: User = Depends(get_current_user),
) -> dict:
    """Report an action result (and the fresh page) to get the next directive."""
    directive = await orchestrator.continue_task(
        task_id=task_id,
        page=payload.page_context,
        result=payload.result,
        confirmation=payload.confirmation,
    )
    if directive.state in TERMINAL_STATES:
        orchestrator.drop_session(task_id)
    return directive.to_dict()


@router.post("/{task_id}/cancel", response_model=DirectiveResponse)
async def cancel_task(
    task_id: str,
    orchestrator: Orchestrator = Depends(get_orchestrator),
    user: User = Depends(get_current_user),
) -> dict:
    """Stop a running task. Idempotent; history is preserved."""
    directive = orchestrator.cancel(task_id)
    orchestrator.drop_session(task_id)
    return directive.to_dict()


@router.get("")
async def list_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    task_status: str | None = Query(default=None, alias="status"),
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    repo = TaskRepository(session, user.id)
    tasks = repo.list(limit=limit, offset=offset, status=task_status)
    return {
        "tasks": [task_to_dict(t, include_actions=False) for t in tasks],
        "total": repo.count(),
    }


@router.get("/{task_id}")
async def get_task(
    task_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict:
    task = TaskRepository(session, user.id).get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    data = task_to_dict(task)
    live = orchestrator.get_session(task_id)
    data["live"] = (
        {"state": live.state, "step": live.step, "warnings": live.warnings} if live else None
    )
    return data


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> Response:
    orchestrator.drop_session(task_id)
    if not TaskRepository(session, user.id).delete(task_id):
        raise HTTPException(status_code=404, detail="Task not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _merge_page(page_context: dict, tab_context) -> dict:  # noqa: ANN001
    """Fill URL/title from the tab when the content script could not run.

    Some pages (the Chrome Web Store, `chrome://` URLs) block content scripts.
    The agent still gets the tab identity and can report that it cannot see the
    page, instead of failing opaquely.
    """
    page = dict(page_context or {})
    if not page.get("url") and tab_context is not None:
        page["url"] = tab_context.url
    if not page.get("title") and tab_context is not None:
        page["title"] = tab_context.title
    if not page.get("domain") and page.get("url"):
        from urllib.parse import urlparse

        try:
            page["domain"] = urlparse(page["url"]).netloc.lower()
        except ValueError:
            page["domain"] = ""
    return page
