"""Deterministic validation of model-proposed actions.

Nothing reaches the browser without passing `validate_action`. The checks here
are the reason a compromised or hallucinating model cannot do damage:

* the action verb must be one of seven known constants;
* the target must exist in the *current* snapshot and be visible and enabled;
* NAVIGATE URLs must be http(s) -- `javascript:`, `data:`, `file:` are rejected;
* typed values are length-capped and stripped of control characters;
* nothing resembling executable script is ever forwarded.

`ValidationOutcome.repair_hint` is fed back to the model on a retry so a
near-miss (e.g. a stale id) can be corrected without failing the task.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from pydantic import ValidationError

from app.browser.action_schema import ActionType, BrowserAction, SemanticPage

_ALLOWED_SCHEMES = {"http", "https"}

# Substrings that must never appear in a value we forward to the page.
_SCRIPT_MARKERS = re.compile(
    r"(javascript\s*:|data\s*:\s*text/html|vbscript\s*:|<\s*script|"
    r"\beval\s*\(|new\s+Function\s*\(|onerror\s*=|onload\s*=)",
    re.IGNORECASE,
)

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

MAX_VALUE_LENGTH = 2000


@dataclass
class ValidationOutcome:
    valid: bool
    action: BrowserAction | None = None
    error: str | None = None
    repair_hint: str | None = None

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "error": self.error,
            "action": self.action.model_dump() if self.action else None,
        }


def _invalid(error: str, hint: str | None = None) -> ValidationOutcome:
    return ValidationOutcome(valid=False, error=error, repair_hint=hint or error)


def validate_url(url: str) -> tuple[bool, str | None]:
    """Allow only absolute http(s) URLs."""
    candidate = (url or "").strip()
    if not candidate:
        return False, "URL is empty"
    if _SCRIPT_MARKERS.search(candidate):
        return False, "URL contains a script scheme or executable payload"
    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        return False, f"URL scheme '{parsed.scheme or 'none'}' is not allowed; use http or https"
    if not parsed.netloc:
        return False, "URL is missing a host"
    return True, None


def sanitize_value(value: str) -> str:
    """Strip control characters and cap length for anything typed into a page."""
    cleaned = _CONTROL_CHARS.sub("", value or "")
    return cleaned[:MAX_VALUE_LENGTH]


def validate_action(raw: dict, page: SemanticPage | None) -> ValidationOutcome:
    """Validate one proposed action against the current page snapshot."""
    if not isinstance(raw, dict):
        return _invalid("Action must be a JSON object")

    try:
        action = BrowserAction.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", ())) or "action"
        msg = first.get("msg", "invalid action")
        return _invalid(
            f"Schema validation failed at '{loc}': {msg}",
            f"Field '{loc}' was invalid ({msg}). Emit only the documented fields.",
        )

    # --- value hygiene ---------------------------------------------------
    if action.value is not None:
        if _SCRIPT_MARKERS.search(action.value):
            return _invalid(
                "Action value contains script-like content and was rejected",
                "Values must be plain text. Never include script, URLs with the "
                "javascript: scheme, or HTML tags.",
            )
        action.value = sanitize_value(action.value)

    # --- per-action rules ------------------------------------------------
    if action.action == ActionType.NAVIGATE:
        ok, err = validate_url(action.value or "")
        if not ok:
            return _invalid(
                f"NAVIGATE rejected: {err}",
                f"{err}. Provide an absolute https:// URL.",
            )
        return ValidationOutcome(valid=True, action=action)

    if action.action in (ActionType.SCROLL, ActionType.WAIT):
        return ValidationOutcome(valid=True, action=action)

    if action.action == ActionType.EXTRACT and not action.target:
        # Whole-page extraction is permitted.
        return ValidationOutcome(valid=True, action=action)

    # --- target must exist in the current snapshot -----------------------
    if page is None:
        return _invalid(
            "No page snapshot is available to validate the target against",
            "Request an updated page observation before targeting elements.",
        )

    elements = page.by_id()
    if not action.target:
        return _invalid(f"{action.action} requires a target element id")

    element = elements.get(action.target)
    if element is None:
        available = ", ".join(list(elements)[:25]) or "none"
        return _invalid(
            f"Element '{action.target}' is not present on the current page",
            f"Element '{action.target}' no longer exists. Choose an id from the "
            f"current page snapshot. Available ids: {available}.",
        )

    if not element.visible:
        return _invalid(
            f"Element '{action.target}' is not visible",
            f"Element '{action.target}' is hidden. Scroll to it or choose a visible element.",
        )

    if element.disabled:
        return _invalid(
            f"Element '{action.target}' is disabled",
            f"Element '{action.target}' is disabled and cannot be actioned.",
        )

    # --- type compatibility ----------------------------------------------
    if action.action == ActionType.TYPE and element.type not in (
        "input",
        "textarea",
        "text",
    ):
        return _invalid(
            f"Cannot TYPE into a '{element.type}' element",
            f"Element '{action.target}' is a {element.type}; TYPE only works on "
            "inputs and textareas.",
        )

    if action.action == ActionType.SELECT:
        if element.type != "select":
            return _invalid(
                f"Cannot SELECT on a '{element.type}' element",
                f"Element '{action.target}' is a {element.type}; SELECT only works "
                "on dropdowns.",
            )
        if element.options and action.value not in element.options:
            opts = ", ".join(element.options[:20])
            return _invalid(
                f"'{action.value}' is not an option of '{action.target}'",
                f"Choose one of the available options for '{action.target}': {opts}.",
            )

    return ValidationOutcome(valid=True, action=action)
