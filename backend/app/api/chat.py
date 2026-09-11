"""Chat entry point.

`/api/chat` is the single door the side panel knocks on. It classifies intent
and routes to one of three places:

* a **direct answer** (`question`, `chitchat`) -- one model call, no page loop.
  This is the common case, and the reason SurfAI reads as an assistant.
* **favourite management** -- answered here from the database.
* a **browsing task** (`browse_task`, or a favourite that implies one) -- starts
  the agent loop and returns the first directive. From then on the extension
  drives it through `/api/tasks/{id}/continue`.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agents.assistant import Assistant
from app.agents.memory_agent import MemoryAgent
from app.agents.orchestrator import COMPLETED, FAILED, Orchestrator
from app.api.deps import get_orchestrator
from app.api.schemas import ChatRequest, DirectiveResponse
from app.api.tasks import _merge_page
from app.database.database import get_db
from app.database.repositories.conversations import ConversationRepository
from app.database.repositories.favourites import FavouriteRepository, to_dict
from app.llm.openai_compatible import get_provider
from app.llm.provider import LLMError, LLMUnavailableError
from app.security.permissions import User, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=DirectiveResponse)
async def chat(
    payload: ChatRequest,
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    orchestrator: Orchestrator = Depends(get_orchestrator),
) -> dict:
    """Answer, then record.

    Recording happens here rather than at each of the handler's several return
    points, so a branch added later cannot forget it.

    Only a finished answer is stored. A directive that starts an agent task is
    not one: its reply arrives later through /api/tasks/{id}/continue, and
    writing the empty message here would leave a half-turn in the transcript.
    Tasks keep their own history, which is a record of what SurfAI did rather
    than of what was said.
    """
    directive = await _chat(payload, session, user, orchestrator)

    if directive.get("type") == "answer":
        page = _merge_page(payload.page_context, payload.tab_context)
        _record(ConversationRepository(user.id), payload, directive, page)

    return directive


async def _chat(
    payload: ChatRequest,
    session: Session,
    user: User,
    orchestrator: Orchestrator,
) -> dict:
    page = _merge_page(payload.page_context, payload.tab_context)
    repo = FavouriteRepository(session, user.id)
    favourites = repo.list_as_dicts()
    memory = MemoryAgent(get_provider())

    try:
        intent = await orchestrator.planner.classify_intent(
            payload.message, favourite_names=[f["name"] for f in favourites]
        )
    except LLMUnavailableError as exc:
        return _error(
            "I could not reach the language model. Check that your LLM runtime is running "
            f"and that LLM_BASE_URL points at it. ({exc})"
        )

    # -- answer directly ---------------------------------------------------
    # The common case. No page loop, no planner, no task record: SurfAI is an
    # assistant first, and only becomes an agent when asked to act.
    if intent.intent in ("question", "chitchat"):
        try:
            reply = await Assistant(get_provider()).answer(
                payload.message,
                page=page,
                history=[turn.model_dump() for turn in payload.history],
            )
        except LLMUnavailableError as exc:
            return _error(f"I could not reach the language model. ({exc})")
        except LLMError as exc:
            logger.info("Direct answer failed: %s", exc)
            return _error("I could not produce an answer. Please try again.")

        return _answer(
            reply.message,
            intent=intent.intent,
            warnings=reply.warnings,
            used_page=reply.used_page,
        )

    # -- list favourites ---------------------------------------------------
    if intent.intent == "list_favourites":
        if not favourites:
            return _answer(
                "You have not saved any favourites yet. Open a page you use often and say "
                '"save this as my ..." to create one.',
                intent=intent.intent,
            )
        listing = "\n".join(
            f"- {f['name']} ({f['domain']}): {f['intent'] or 'no stated intent'}"
            for f in favourites[:20]
        )
        return _answer(
            f"You have {len(favourites)} saved favourite(s):\n{listing}",
            intent=intent.intent,
            favourites=favourites,
        )

    # -- save a favourite --------------------------------------------------
    if intent.intent == "save_favourite":
        if not page.get("url"):
            return _answer(
                "I need an open web page to save. Open the page you want to remember, "
                "then ask again.",
                intent=intent.intent,
            )
        try:
            draft = await memory.draft_favourite(payload.message, page)
        except LLMError as exc:
            logger.info("Falling back to heuristic favourite draft: %s", exc)
            draft = await MemoryAgent(None).draft_favourite(payload.message, page)

        record = repo.create(draft)
        saved = to_dict(record)
        preference_note = ""
        if saved["preferences"]:
            rendered = ", ".join(f"{k}: {v}" for k, v in saved["preferences"].items())
            preference_note = f" Preferences recorded: {rendered}."
        return _answer(
            f"Saved \"{saved['name']}\" for {saved['domain']}. "
            f"Intent: {saved['intent']}.{preference_note} "
            f"You can say \"open my {saved['name'].lower()}\" later.",
            intent=intent.intent,
            favourite=saved,
        )

    # -- use a favourite ---------------------------------------------------
    favourite = None
    if intent.intent == "use_favourite":
        query = intent.favourite_reference or payload.message
        match = await memory.resolve(query, favourites)
        if not match.found:
            if not favourites:
                return _answer(
                    "You have not saved any favourites yet, so there is nothing to open.",
                    intent=intent.intent,
                )
            names = ", ".join(f["name"] for f in favourites[:10])
            return _answer(
                f"I could not tell which favourite you meant. You have: {names}.",
                intent=intent.intent,
                favourites=favourites,
            )
        favourite = match.favourite

    # -- browse ------------------------------------------------------------
    try:
        directive = await orchestrator.start(
            task_id=payload.task_id or str(uuid.uuid4()),
            message=payload.message,
            page=page,
            favourite=favourite,
            user_id=user.id,
        )
    except LLMUnavailableError as exc:
        return _error(f"I could not reach the language model. ({exc})")

    data = directive.to_dict()
    data["intent"] = intent.intent
    if favourite:
        data["favourite"] = favourite
        # When the favourite lives elsewhere, the first real step is getting there.
        if favourite.get("url") and not _same_page(page.get("url", ""), favourite["url"]):
            data["favourite_navigation"] = favourite["url"]
    return data


def _record(
    history: ConversationRepository,
    payload: ChatRequest,
    directive: dict,
    page: dict,
) -> dict:
    """Store the exchange and stamp the directive with where it landed.

    Never raises. History is a convenience, and a convenience that can swallow
    the answer the user is waiting for is not one; a failure here is logged and
    the reply goes out unchanged, minus the id.
    """
    try:
        directive["conversation_id"] = history.record(
            conversation_id=payload.conversation_id,
            user_message=payload.message,
            reply=directive.get("message", ""),
            page_url=page.get("url") or None,
            warnings=list(directive.get("warnings") or []),
        )
    except Exception:  # noqa: BLE001 - the answer matters more than the record
        logger.exception("Could not record the conversation")
    return directive


def _answer(message: str, **extra) -> dict:
    payload = {
        "type": "answer",
        "task_id": "",
        "state": COMPLETED,
        "activity": "Answered",
        "step": 0,
        "max_steps": 0,
        "action": None,
        "risk": None,
        "message": message,
        "data": None,
        "warnings": [],
    }
    payload.update(extra)
    return payload


def _error(message: str) -> dict:
    return {
        "type": "error",
        "task_id": "",
        "state": FAILED,
        "activity": "Task failed",
        "step": 0,
        "max_steps": 0,
        "action": None,
        "risk": None,
        "message": message,
        "data": None,
        "warnings": [],
    }


def _same_page(current: str, target: str) -> bool:
    from urllib.parse import urlparse

    try:
        a, b = urlparse(current), urlparse(target)
    except ValueError:
        return False
    return bool(a.netloc) and a.netloc == b.netloc and a.path.rstrip("/") == b.path.rstrip("/")
