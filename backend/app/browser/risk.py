"""Deterministic risk classification.

This module is the security decision point for human-in-the-loop confirmation.
It is **pure Python over structured data**: no model output influences the
verdict. A fully prompt-injected planner still cannot escalate its own
privileges, because the answer to "does this need confirmation?" is computed
here from the action and the element it targets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.config import settings

LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"

# Actions that are read-only or purely navigational.
_INHERENTLY_LOW = {"EXTRACT", "SCROLL", "WAIT"}

# Ordered most-severe first: the first category to match wins.
_CATEGORY_PATTERNS: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "PURCHASE",
        HIGH,
        re.compile(
            r"\b(buy now|buy it now|place order|place your order|complete purchase|"
            r"confirm order|checkout|check out|proceed to pay|order now|purchase|"
            r"subscribe now|start subscription|upgrade plan)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "PAYMENT",
        HIGH,
        re.compile(
            r"\b(pay now|make payment|confirm payment|authorise payment|authorize payment|"
            r"send money|transfer funds|add card|save card|billing details|"
            r"card number|cvv|upi|net ?banking)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "DELETE",
        HIGH,
        re.compile(
            r"\b(delete|remove permanently|permanently delete|erase|destroy|"
            r"clear all|wipe|deactivate|close account|cancel subscription)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "ACCOUNT_CHANGES",
        HIGH,
        re.compile(
            r"\b(change password|update password|reset password|change email|"
            r"update email|change phone|two[- ]factor|2fa|security settings|"
            r"privacy settings|transfer ownership|add member|revoke access)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "LOGIN",
        MEDIUM,
        re.compile(
            r"\b(log ?in|sign ?in|sign ?up|register|create account|continue with google|"
            r"continue with github|authenticate|verify identity|otp)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "UPLOAD_FILE",
        MEDIUM,
        re.compile(r"\b(upload|attach file|choose file|browse files|drop file)\b", re.IGNORECASE),
    ),
    (
        "SEND_MESSAGE",
        MEDIUM,
        re.compile(
            r"\b(send message|send email|post comment|post reply|publish|tweet|"
            r"send request|contact seller|message seller|send invite)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "SUBMIT_FORM",
        MEDIUM,
        re.compile(
            r"\b(submit|apply now|easy apply|send application|confirm and continue|"
            r"place request|book now|reserve|rsvp|save changes)\b",
            re.IGNORECASE,
        ),
    ),
]

# Search / filter submits look like SUBMIT_FORM but are read-only in effect.
_BENIGN_SUBMIT = re.compile(
    r"\b(search|find|filter|apply filters?|sort|go|show results|refine|browse|"
    r"view (more|all)|load more|next page|previous page)\b",
    re.IGNORECASE,
)

_FILTERISH = re.compile(r"filter|sort|refine", re.IGNORECASE)

_EXPLANATIONS = {
    "PURCHASE": "This may complete a purchase or place an order on {domain}.",
    "PAYMENT": "This may authorise a payment or submit payment details to {domain}.",
    "DELETE": "This may permanently delete data on {domain}.",
    "ACCOUNT_CHANGES": "This may change account or security settings on {domain}.",
    "LOGIN": "This interacts with a sign-in or registration flow on {domain}.",
    "UPLOAD_FILE": "This may upload a file to {domain}.",
    "SEND_MESSAGE": "This may send a message or publish content on {domain}.",
    "SUBMIT_FORM": "This will submit information to {domain}.",
    "NAVIGATE": "This will open a different page ({value}).",
    "TYPE_SENSITIVE": "This types into a sensitive field on {domain}.",
    "READ_PAGE": "This reads information from the page without changing anything.",
    "SEARCH": "This performs a search on {domain}.",
    "FILTER": "This applies a filter on {domain}.",
    "INTERACT": "This interacts with the page on {domain}.",
}


@dataclass
class RiskAssessment:
    level: str
    requires_confirmation: bool
    category: str
    explanation: str

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "requires_confirmation": self.requires_confirmation,
            "category": self.category,
            "explanation": self.explanation,
        }


def _element_text(element: dict | None) -> str:
    if not element:
        return ""
    keys = ("text", "ariaLabel", "placeholder", "name", "value", "role", "href")
    return " ".join(str(element.get(k, "")) for k in keys if element.get(k))


def _is_external(url: str | None, current_url: str | None) -> bool:
    if not url or not current_url:
        return False
    try:
        target = urlparse(url).netloc.lower()
        current = urlparse(current_url).netloc.lower()
    except ValueError:
        return True
    return target not in ("", current)


def classify(
    action: dict,
    element: dict | None = None,
    *,
    domain: str = "this website",
    current_url: str | None = None,
) -> RiskAssessment:
    """Classify a single proposed action.

    `element` is the semantic element the action targets, taken from the
    *current snapshot* -- not from the model.
    """
    action_type = str(action.get("action", "")).upper()
    value = str(action.get("value") or "")

    if action_type in _INHERENTLY_LOW:
        return RiskAssessment(
            LOW, False, "READ_PAGE", _EXPLANATIONS["READ_PAGE"].format(domain=domain)
        )

    if action_type == "NAVIGATE":
        # Navigating off-site is worth confirming: it is how an injected page
        # would try to move the agent somewhere it was not asked to go.
        external = _is_external(value, current_url)
        return RiskAssessment(
            MEDIUM if external else LOW,
            external,
            "NAVIGATE",
            _EXPLANATIONS["NAVIGATE"].format(value=value[:120] or "a new page"),
        )

    haystack = f"{_element_text(element)} {value}"

    if action_type == "TYPE" and element and element.get("sensitive"):
        return RiskAssessment(
            MEDIUM, True, "LOGIN", _EXPLANATIONS["TYPE_SENSITIVE"].format(domain=domain)
        )

    for category, level, pattern in _CATEGORY_PATTERNS:
        if not pattern.search(haystack):
            continue
        # A "Search" button inside a form should not be treated as a submit.
        if category == "SUBMIT_FORM" and _BENIGN_SUBMIT.search(haystack):
            break
        requires = level in (MEDIUM, HIGH) or category in settings.always_confirm_set
        return RiskAssessment(
            level, requires, category, _EXPLANATIONS[category].format(domain=domain)
        )

    if _BENIGN_SUBMIT.search(haystack):
        category = "FILTER" if _FILTERISH.search(haystack) else "SEARCH"
        return RiskAssessment(LOW, False, category, _EXPLANATIONS[category].format(domain=domain))

    if action_type in ("CLICK", "SELECT", "TYPE"):
        return RiskAssessment(
            LOW, False, "INTERACT", _EXPLANATIONS["INTERACT"].format(domain=domain)
        )

    # Unknown action types are never auto-executed.
    return RiskAssessment(HIGH, True, "UNKNOWN", f"Unrecognised action '{action_type}'.")


def classify_plan(
    actions: list[dict],
    elements_by_id: dict[str, dict],
    *,
    domain: str = "this website",
    current_url: str | None = None,
) -> list[RiskAssessment]:
    return [
        classify(
            a,
            elements_by_id.get(str(a.get("target") or "")),
            domain=domain,
            current_url=current_url,
        )
        for a in actions
    ]
