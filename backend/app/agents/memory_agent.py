"""Memory Agent: AI-aware favourites.

A favourite stores *why* the user goes somewhere, not just where. That is what
lets "check my AI jobs" become a real task: the intent and preferences travel
with the URL and are injected into the planner as trusted user context.

Resolution is lexical-first (fast, deterministic, works offline) with an LLM
tie-break only when scoring is ambiguous. The scoring function is pure and
directly unit-tested, so favourite retrieval keeps working when no model is
reachable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from app.llm.prompts import MEMORY_SYSTEM
from app.llm.provider import LLMError, LLMProvider, Message

logger = logging.getLogger(__name__)

_STOPWORDS = {
    "a", "an", "the", "my", "me", "i", "open", "check", "show", "go", "to", "on",
    "for", "in", "of", "and", "or", "please", "can", "you", "use", "using", "saved",
    "favourite", "favourites", "favorite", "favorites", "bookmark", "bookmarks",
    "search", "find", "get", "let", "s", "up", "look", "again", "from", "with",
}

_WORD = re.compile(r"[a-z0-9]+")

FAVOURITE_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "intent", "description"],
    "additionalProperties": False,
    "properties": {
        "name": {"type": "string", "maxLength": 60},
        "intent": {"type": "string", "maxLength": 300},
        "description": {"type": "string", "maxLength": 300},
        "preferences": {"type": "object"},
    },
}

FAVOURITE_MATCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["favourite_id"],
    "additionalProperties": False,
    "properties": {
        "favourite_id": {"type": "string"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
}

# Below this, a lexical match is not trusted on its own.
MATCH_THRESHOLD = 0.34
# Gap between best and runner-up that makes a lexical win unambiguous.
DECISIVE_MARGIN = 0.15


@dataclass
class FavouriteMatch:
    favourite: dict[str, Any] | None
    score: float
    method: str
    alternatives: list[dict[str, Any]]

    @property
    def found(self) -> bool:
        return self.favourite is not None


def _stem(word: str) -> str:
    """Light plural normalisation.

    Users say "my AI jobs" and "my job search" interchangeably; without this,
    `job` and `jobs` score as unrelated tokens and a clear reference misses.
    Deliberately not a real stemmer -- just enough to make plurals match.
    """
    if len(word) > 3:
        if word.endswith("ies"):
            return word[:-3] + "y"
        if word.endswith("ses") or word.endswith("xes") or word.endswith("ches"):
            return word[:-2]
        if word.endswith("s") and not word.endswith("ss"):
            return word[:-1]
    return word


def _tokens(text: str) -> set[str]:
    return {
        _stem(w)
        for w in _WORD.findall((text or "").lower())
        if w not in _STOPWORDS and len(w) > 1
    }


def score_favourite(query: str, favourite: dict[str, Any]) -> float:
    """Score 0..1 for how well a favourite answers a natural-language reference.

    Weighted by field trustworthiness: the name the user chose is the strongest
    signal, then their stated intent, then description and preferences.
    """
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0

    fields = (
        (_tokens(favourite.get("name", "")), 1.0),
        (_tokens(favourite.get("intent", "")), 0.55),
        (_tokens(favourite.get("description", "")), 0.35),
        (_tokens(str(favourite.get("preferences", ""))), 0.25),
        (_tokens(favourite.get("domain", "")), 0.45),
    )

    score = 0.0
    for tokens, weight in fields:
        if not tokens:
            continue
        overlap = query_tokens & tokens
        if overlap:
            score += weight * (len(overlap) / len(query_tokens))

    # An exact name appearing verbatim in the query is close to conclusive.
    name = (favourite.get("name") or "").lower().strip()
    if name and name in (query or "").lower():
        score += 1.0

    return min(score, 1.0) if score <= 1.0 else 1.0


class MemoryAgent:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider

    # -- retrieval --------------------------------------------------------

    def rank(self, query: str, favourites: list[dict[str, Any]]) -> list[tuple[float, dict]]:
        scored = [(score_favourite(query, f), f) for f in favourites]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return scored

    async def resolve(
        self, query: str, favourites: list[dict[str, Any]], *, use_llm: bool = True
    ) -> FavouriteMatch:
        """Resolve a natural-language reference to one saved favourite."""
        if not favourites:
            return FavouriteMatch(None, 0.0, "none", [])

        scored = self.rank(query, favourites)
        best_score, best = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        alternatives = [f for _, f in scored[1:4]]

        decisive = best_score >= MATCH_THRESHOLD and (best_score - runner_up) >= DECISIVE_MARGIN
        if decisive:
            return FavouriteMatch(best, best_score, "lexical", alternatives)

        if use_llm and self.provider is not None and best_score > 0:
            llm_match = await self._resolve_llm(query, scored[:6])
            if llm_match is not None:
                return FavouriteMatch(llm_match, max(best_score, 0.5), "llm", alternatives)

        if best_score >= MATCH_THRESHOLD:
            return FavouriteMatch(best, best_score, "lexical-weak", alternatives)

        return FavouriteMatch(None, best_score, "no-match", [f for _, f in scored[:3]])

    async def _resolve_llm(
        self, query: str, candidates: list[tuple[float, dict[str, Any]]]
    ) -> dict[str, Any] | None:
        listing = "\n".join(
            f"- id={f['id']} | name={f.get('name')} | intent={f.get('intent')} "
            f"| domain={f.get('domain')}"
            for _, f in candidates
        )
        messages = [
            Message(role="system", content=MEMORY_SYSTEM),
            Message(
                role="user",
                content=(
                    f'The user said: "{query}"\n\n'
                    f"Their saved favourites:\n{listing}\n\n"
                    "Which one are they referring to? Reply with its id in "
                    '`favourite_id`, or an empty string if none of them match.'
                ),
            ),
        ]
        try:
            parsed = await self.provider.generate_structured(
                messages, schema=FAVOURITE_MATCH_SCHEMA, schema_name="favourite_match",
                max_retries=0,
            )
        except LLMError as exc:
            logger.info("LLM favourite matching unavailable: %s", exc)
            return None

        favourite_id = str(parsed.get("favourite_id") or "").strip()
        if not favourite_id:
            return None
        # Only accept an id we actually offered.
        return next((f for _, f in candidates if f["id"] == favourite_id), None)

    # -- creation ---------------------------------------------------------

    async def draft_favourite(
        self,
        user_message: str,
        page: dict[str, Any],
        *,
        conversation: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Turn "save this as my research reading" into a structured favourite."""
        url = page.get("url", "")
        domain = _domain_of(url)
        fallback = {
            "name": _fallback_name(user_message, page),
            "url": url,
            "domain": domain,
            "intent": user_message.strip()[:300],
            "description": (page.get("title") or domain or "Saved page")[:300],
            "preferences": {},
            "metadata": {"source": "heuristic", "page_title": page.get("title", "")},
        }

        if self.provider is None:
            return fallback

        recent = ""
        if conversation:
            recent = "\n".join(
                f"{turn.get('role')}: {str(turn.get('content'))[:200]}"
                for turn in conversation[-4:]
            )

        messages = [
            Message(role="system", content=MEMORY_SYSTEM),
            Message(
                role="user",
                content=(
                    f'The user said: "{user_message}"\n\n'
                    f"Current page: {page.get('title', '')} ({url})\n"
                    f"Page excerpt: {str(page.get('summary', ''))[:600]}\n"
                    + (f"\nRecent conversation:\n{recent}\n" if recent else "")
                    + "\nCreate a favourite. `name` is a short label the user would "
                    "recognise and could say out loud later. `intent` states what they "
                    "want to achieve when they return. `preferences` captures concrete "
                    "constraints they mentioned (topic, location, timeframe, sources, "
                    "or anything else specific) as key/value pairs -- leave it empty "
                    "if they stated none. Do not invent preferences."
                ),
            ),
        ]
        try:
            parsed = await self.provider.generate_structured(
                messages, schema=FAVOURITE_DRAFT_SCHEMA, schema_name="favourite_draft"
            )
        except LLMError as exc:
            logger.info("LLM favourite drafting unavailable: %s", exc)
            return fallback

        preferences = parsed.get("preferences") or {}
        if not isinstance(preferences, dict):
            preferences = {}

        return {
            "name": str(parsed.get("name") or fallback["name"])[:200],
            "url": url,
            "domain": domain,
            "intent": str(parsed.get("intent") or fallback["intent"])[:1000],
            "description": str(parsed.get("description") or fallback["description"])[:1000],
            "preferences": preferences,
            "metadata": {"source": "llm", "page_title": page.get("title", "")},
        }

    @staticmethod
    def build_goal(favourite: dict[str, Any], user_message: str) -> str:
        """Compose the effective goal when a task runs against a favourite."""
        intent = (favourite.get("intent") or "").strip()
        preferences = favourite.get("preferences") or {}
        parts = [user_message.strip()]
        if intent:
            parts.append(f"Saved intent for '{favourite.get('name')}': {intent}")
        if preferences:
            rendered = ", ".join(f"{k}: {v}" for k, v in list(preferences.items())[:10])
            parts.append(f"Saved preferences: {rendered}")
        return "\n".join(parts)


def _domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""


def _fallback_name(user_message: str, page: dict[str, Any]) -> str:
    """Pull a name out of phrasing like 'save this as my research reading'."""
    match = re.search(
        r"\bas\s+(?:my\s+|the\s+)?[\"']?([^\"'.,\n]{2,50})[\"']?", user_message, re.IGNORECASE
    )
    if match:
        candidate = match.group(1).strip().strip(".")
        candidate = re.sub(r"\bfavourite\b|\bfavorite\b|\bbookmark\b", "", candidate, flags=re.I)
        candidate = candidate.strip()
        if candidate:
            return candidate[:60].title()
    title = (page.get("title") or "").strip()
    return (title or _domain_of(page.get("url", "")) or "Saved page")[:60]
