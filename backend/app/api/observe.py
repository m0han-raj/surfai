"""Page observation without starting a task.

Backs the side panel's "Current Page" card: the user gets an immediate read of
what SurfAI can see and do on the page, with no model call and no cost.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.agents.page_agent import PageAgent
from app.agents.tool_discovery import ToolDiscovery
from app.api.schemas import ObserveRequest
from app.security.permissions import User, get_current_user

router = APIRouter(prefix="/api", tags=["observe"])


@router.post("/observe")
async def observe(
    payload: ObserveRequest,
    user: User = Depends(get_current_user),
) -> dict:
    understanding, page, scan = PageAgent().analyze(payload.page_context)
    discovery = await ToolDiscovery().discover(understanding, page, use_llm=False)
    return {
        "page": understanding.to_dict(),
        "tools": discovery["tools"],
        "security": scan.to_dict(),
        "element_count": len(page.elements),
        "truncated": page.truncated,
    }
