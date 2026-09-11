"""Page Agent: turns a raw snapshot into an understanding of the page.

Deliberately heuristic-first. Classifying "this page has a search box and a
result grid" does not need a language model, and doing it in Python makes the
agent loop faster, cheaper and deterministic. The LLM is consulted only for the
prose summary, and only when a caller asks for it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.browser.action_schema import SemanticPage
from app.llm.prompts import PAGE_AGENT_SYSTEM, format_page_context
from app.llm.provider import LLMError, LLMProvider, Message
from app.security.prompt_injection import InjectionScan
from app.security.sanitizer import sanitize_page

logger = logging.getLogger(__name__)


class MalformedPageError(ValueError):
    """The caller sent a page snapshot that does not parse.

    Distinguished from an internal failure because it is the caller's to fix:
    `page_context` is typed loosely at the API boundary so that an unfamiliar
    key does not break the request, which leaves this as the first place a
    genuinely wrong shape is noticed. Reporting it as a server error tells
    whoever is debugging a version-skewed content script to look in entirely
    the wrong place.
    """

_SEARCH_HINT = re.compile(r"search|find|query|look ?up|keyword", re.IGNORECASE)
_FILTER_HINT = re.compile(r"filter|sort|refine|price|category|brand|rating|range", re.IGNORECASE)
_PAGINATION_HINT = re.compile(
    r"next|previous|prev|page \d|load more|show more|older|newer", re.IGNORECASE
)
_RESULT_HINT = re.compile(r"result|product|item|job|listing|card|post|article", re.IGNORECASE)
_AUTH_HINT = re.compile(r"log ?in|sign ?in|sign ?up|register|password|account", re.IGNORECASE)


@dataclass
class PageUnderstanding:
    """What the agent knows about the current page."""

    url: str
    domain: str
    title: str
    page_type: str
    summary: str
    has_search: bool = False
    has_filters: bool = False
    has_pagination: bool = False
    has_results: bool = False
    requires_auth: bool = False
    search_input_ids: list[str] = field(default_factory=list)
    search_button_ids: list[str] = field(default_factory=list)
    filter_ids: list[str] = field(default_factory=list)
    pagination_ids: list[str] = field(default_factory=list)
    result_link_ids: list[str] = field(default_factory=list)
    element_count: int = 0
    injection: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "domain": self.domain,
            "title": self.title,
            "page_type": self.page_type,
            "summary": self.summary,
            "capabilities": {
                "search": self.has_search,
                "filters": self.has_filters,
                "pagination": self.has_pagination,
                "results": self.has_results,
                "auth": self.requires_auth,
            },
            "element_count": self.element_count,
            "injection": self.injection,
        }


def _describe(exc: ValidationError) -> str:
    """The first problem, in the caller's terms.

    Names the field and what was wrong with it, and nothing about which model
    of ours rejected it or where pydantic documents the rule.
    """
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(part) for part in first.get("loc", ())) or "page_context"
    return f"{field} - {first.get('msg', 'validation failed')}"


def validate_page_snapshot(raw_page: dict[str, Any] | None) -> None:
    """Raise `MalformedPageError` if this snapshot will not parse.

    Callers that only read the raw dict still need the shape checked, and a
    caller that goes on to `analyze` pays one cheap extra parse for the
    guarantee that every entry point rejects the same input the same way.
    """
    if not raw_page:
        # No page open, or a page that blocks content scripts. Normal.
        return
    try:
        SemanticPage.model_validate(raw_page)
    except ValidationError as exc:
        raise MalformedPageError(_describe(exc)) from exc


def _label(element: dict[str, Any]) -> str:
    keys = ("text", "ariaLabel", "placeholder", "name", "role", "id")
    return " ".join(str(element.get(k, "")) for k in keys if element.get(k))


class PageAgent:
    """Analyses a sanitised page snapshot."""

    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider

    def analyze(
        self, raw_page: dict[str, Any]
    ) -> tuple[PageUnderstanding, SemanticPage, InjectionScan]:
        """Sanitise, then classify. Returns understanding, clean page and scan."""
        clean, scan = sanitize_page(raw_page or {})
        try:
            page = SemanticPage.model_validate(clean)
        except ValidationError as exc:
            raise MalformedPageError(_describe(exc)) from exc

        understanding = PageUnderstanding(
            url=page.url,
            domain=page.domain or _domain_of(page.url),
            title=page.title,
            page_type="unknown",
            summary=page.summary[:600],
            element_count=len(page.elements),
            injection=scan.to_dict(),
        )

        for element in clean.get("elements", []):
            etype = element.get("type")
            label = _label(element)

            if etype in ("input", "textarea"):
                input_type = str(element.get("inputType", "")).lower()
                if input_type == "search" or _SEARCH_HINT.search(label):
                    understanding.search_input_ids.append(element["id"])
                elif _FILTER_HINT.search(label) or input_type in ("range", "number", "checkbox"):
                    understanding.filter_ids.append(element["id"])
            elif etype == "select":
                understanding.filter_ids.append(element["id"])
            elif etype in ("checkbox", "radio"):
                understanding.filter_ids.append(element["id"])
            elif etype == "button":
                if _SEARCH_HINT.search(label):
                    understanding.search_button_ids.append(element["id"])
                elif _PAGINATION_HINT.search(label):
                    understanding.pagination_ids.append(element["id"])
                elif _FILTER_HINT.search(label):
                    understanding.filter_ids.append(element["id"])
            elif etype == "link":
                if _PAGINATION_HINT.search(label):
                    understanding.pagination_ids.append(element["id"])
                elif _RESULT_HINT.search(label) or _RESULT_HINT.search(
                    str(element.get("href", ""))
                ):
                    understanding.result_link_ids.append(element["id"])

            if _AUTH_HINT.search(label):
                understanding.requires_auth = True

        understanding.has_search = bool(understanding.search_input_ids)
        understanding.has_filters = bool(understanding.filter_ids)
        understanding.has_pagination = bool(understanding.pagination_ids)
        understanding.has_results = bool(understanding.result_link_ids) or bool(
            _RESULT_HINT.search(page.summary)
        )
        understanding.page_type = _classify_page_type(understanding, page)

        return understanding, page, scan

    async def summarize(self, page: SemanticPage, goal: str) -> str:
        """Optional LLM prose summary. Falls back to the heuristic excerpt."""
        if self.provider is None:
            return page.summary[:400]
        messages = [
            Message(role="system", content=PAGE_AGENT_SYSTEM),
            Message(
                role="user",
                content=(
                    f"The user's goal: {goal}\n\n"
                    "Summarise the page below in at most three sentences, focusing on "
                    "what is relevant to that goal.\n\n"
                    f"{format_page_context(page.model_dump())}"
                ),
            ),
        ]
        try:
            response = await self.provider.generate(messages, max_tokens=300)
            return response.content.strip()[:800]
        except LLMError as exc:
            logger.info("Page summary unavailable: %s", exc)
            return page.summary[:400]


def _classify_page_type(u: PageUnderstanding, page: SemanticPage) -> str:
    haystack = f"{u.title} {page.url} {u.summary[:200]}".lower()
    if u.requires_auth and not u.has_results and len(page.elements) < 25:
        return "auth"
    if u.has_results and (u.has_filters or u.has_pagination):
        return "results"
    if re.search(r"/(product|item|p)/|/jobs?/\d|/dp/", page.url, re.IGNORECASE):
        return "detail"
    if u.has_search and not u.has_results:
        return "search"
    if re.search(r"article|blog|news|post|paper|/read", haystack):
        return "article"
    if u.has_results:
        return "results"
    return "content"


def _domain_of(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""
