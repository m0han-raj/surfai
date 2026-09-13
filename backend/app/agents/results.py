"""Turning extracted page items into the cards the panel renders.

One rule holds the design together: the model chooses which results answer the
question, by index, and every field shown comes from the page. Asking a model
to repeat a price invites it to get one wrong, and a wrong price on a shopping
card is not a cosmetic defect. An index cannot be a fabricated price.

Which makes this module's real job the unglamorous one: the items arrive from a
web page, through a tool result, and nothing about them is trustworthy. Links
are checked, lengths are capped, and anything malformed is dropped rather than
patched up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

#: Schemes a card's link or image may use. An allowlist, matching the one the
#: panel applies to links inside a reply: these urls have the same provenance.
SAFE_SCHEMES = frozenset({"http", "https"})

#: Cards shown at once. Forty is a wall rather than an answer, and the model
#: was asked to choose, so a long selection means it did not. Ten is already a
#: scroll in a four-hundred-pixel panel.
MAX_RESULTS = 10

MAX_TITLE = 300
MAX_PRICE = 100
MAX_META_LINE = 200
MAX_META_LINES = 3


def _safe_url(value: Any) -> str | None:
    """The url, if it is one we are willing to put behind a click."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        if urlparse(value).scheme.lower() not in SAFE_SCHEMES:
            return None
    except ValueError:
        return None
    return value.strip()


@dataclass
class ResultItem:
    """One card. Every field is as the page printed it."""

    title: str
    price: str | None = None
    image: str | None = None
    url: str | None = None
    meta: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "price": self.price,
            "image": self.image,
            "url": self.url,
            "meta": self.meta,
        }


def _to_item(raw: Any) -> ResultItem | None:
    if not isinstance(raw, dict):
        return None

    title = str(raw.get("title") or "").strip()
    if not title:
        # A card with nothing to read is worse than one card fewer.
        return None

    price = str(raw.get("price") or "").strip() or None
    meta = [
        str(line).strip()[:MAX_META_LINE]
        for line in (raw.get("meta") or [])
        if str(line).strip()
    ][:MAX_META_LINES]

    return ResultItem(
        title=title[:MAX_TITLE],
        price=price[:MAX_PRICE] if price else None,
        image=_safe_url(raw.get("image")),
        url=_safe_url(raw.get("url")),
        meta=meta,
    )


def select_results(items: Any, indices: list[int] | None) -> list[ResultItem]:
    """The items the model picked, in the order it picked them.

    An index outside the list is dropped rather than raised on or wrapped
    round: a model that miscounts should show one card fewer, never a
    different product than the one it meant.

    No selection is not a request to show everything. A model that omitted the
    field has not said anything about these results, and filling the panel with
    forty cards on that basis would be inventing an answer.
    """
    if not isinstance(items, list) or not indices:
        return []

    chosen: list[ResultItem] = []
    seen: set[int] = set()

    for index in indices:
        if not isinstance(index, int) or index in seen:
            continue
        if index < 0 or index >= len(items):
            continue
        seen.add(index)

        item = _to_item(items[index])
        if item is not None:
            chosen.append(item)
        if len(chosen) >= MAX_RESULTS:
            break

    return chosen
