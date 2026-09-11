"""Sanitisation of untrusted page data before it reaches the model or the DB.

Two responsibilities:

* Strip *values* that must never be persisted or sent to a model -- password
  fields, anything that looks like a credential, card number or OTP.
* Wrap untrusted content in an explicit, non-forgeable data envelope.
"""

from __future__ import annotations

import re
from typing import Any

from app.config import settings
from app.security.prompt_injection import InjectionScan, scan

_SENSITIVE_INPUT_TYPES = {"password", "hidden"}

# `[ _-]?` rather than `[_-]?`: real labels read "Card number" and "API Key".
_SENSITIVE_NAME = re.compile(
    r"(pass(word|wd)?|pwd|secret|token|api[ _-]?key|otp|cvv|cvc|card[ _-]?(number|no)|"
    r"ssn|aadhaar|pan[ _-]?number|security[ _-]?code|auth|credit[ _-]?card)",
    re.IGNORECASE,
)

# Long digit runs that look like payment instruments.
_CARD_LIKE = re.compile(r"\b(?:\d[ -]?){13,19}\b")

ENVELOPE_OPEN = "<WEBPAGE_DATA>"
ENVELOPE_CLOSE = "</WEBPAGE_DATA>"

# A page could print our own envelope tags to try to "close" the data block
# early and have following text read as trusted. Escape them first.
_ENVELOPE_ESCAPE = re.compile(r"</?WEBPAGE_DATA>|</?TOOL_RESULT>", re.IGNORECASE)


def is_sensitive_field(element: dict[str, Any]) -> bool:
    """True when an element's value must never be captured."""
    if str(element.get("inputType", "")).lower() in _SENSITIVE_INPUT_TYPES:
        return True
    haystack = " ".join(
        str(element.get(k, ""))
        for k in ("name", "ariaLabel", "placeholder", "id", "text", "role")
    )
    return bool(_SENSITIVE_NAME.search(haystack))


def redact_text(text: str) -> str:
    """Remove credential-shaped substrings from free text."""
    if not text:
        return ""
    return _CARD_LIKE.sub("[redacted-number]", text)


def sanitize_element(element: dict[str, Any]) -> dict[str, Any]:
    """Copy an element with sensitive values removed.

    Structure is preserved so the agent can still *use* a password field
    (type into it under confirmation) without its content being observable.
    """
    clean = dict(element)
    if is_sensitive_field(clean):
        clean.pop("value", None)
        clean["sensitive"] = True
    elif "value" in clean and isinstance(clean["value"], str):
        clean["value"] = redact_text(clean["value"])[:200]

    for key in ("text", "placeholder", "ariaLabel", "label"):
        if isinstance(clean.get(key), str):
            clean[key] = redact_text(clean[key])[:200]
    return clean


def sanitize_page(page: dict[str, Any]) -> tuple[dict[str, Any], InjectionScan]:
    """Sanitise a semantic page snapshot.

    Returns the cleaned page plus the injection scan so the caller can record
    that a page was hostile and surface it to the user.
    """
    clean = dict(page)
    elements = [sanitize_element(e) for e in page.get("elements", []) or []]

    # Scan the concatenation of everything the model will read.
    readable = " \n".join(
        filter(
            None,
            [
                str(page.get("title", "")),
                str(page.get("summary", "")),
                *[
                    " ".join(
                        str(e.get(k, ""))
                        for k in ("text", "placeholder", "ariaLabel")
                        if e.get(k)
                    )
                    for e in elements
                ],
            ],
        )
    )
    result = scan(readable)

    if result.is_suspicious:
        # Redact in-place so the model reads the neutralised form.
        for e in elements:
            for key in ("text", "placeholder", "ariaLabel"):
                if isinstance(e.get(key), str):
                    e[key] = scan(e[key]).sanitized
        clean["summary"] = scan(str(page.get("summary", ""))).sanitized
        clean["title"] = scan(str(page.get("title", ""))).sanitized
    else:
        clean["summary"] = str(page.get("summary", ""))

    clean["summary"] = redact_text(clean["summary"])[: settings.max_page_summary_chars]
    clean["elements"] = elements
    return clean, result


def escape_envelope(text: str) -> str:
    """Prevent untrusted text from forging envelope boundaries."""
    return _ENVELOPE_ESCAPE.sub("[tag-removed]", text or "")


# An allowlist, not a filter: the set of envelope labels is fixed by SurfAI, so
# neither a caller nor a page can introduce a new one.
ALLOWED_LABELS = frozenset({"WEBPAGE_DATA", "TOOL_RESULT"})


def wrap_untrusted(text: str, label: str = "WEBPAGE_DATA") -> str:
    """Wrap untrusted content in a labelled, escaped envelope."""
    safe_label = label.upper() if label.upper() in ALLOWED_LABELS else "WEBPAGE_DATA"
    return f"<{safe_label}>\n{escape_envelope(text)}\n</{safe_label}>"
