"""Action schema and validator: the deterministic gate on model output."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.browser.action_schema import ActionType, BrowserAction, SemanticPage
from app.browser.validation import MAX_VALUE_LENGTH, validate_action, validate_url

SHARED_SCHEMA = Path(__file__).resolve().parents[2] / "shared" / "action-schema.ts"


@pytest.fixture
def page(product_page) -> SemanticPage:
    return SemanticPage.model_validate(product_page)


# --- contract ------------------------------------------------------------


def test_action_vocabulary_matches_shared_schema() -> None:
    """The TS and Python action vocabularies must not drift."""
    source = SHARED_SCHEMA.read_text(encoding="utf-8")
    block = re.search(r"ACTION_TYPES\s*=\s*\[(.*?)\]", source, re.DOTALL)
    assert block, "ACTION_TYPES not found in shared/action-schema.ts"
    ts_actions = set(re.findall(r"'([A-Z_]+)'", block.group(1)))
    assert ts_actions == {a.value for a in ActionType}


# --- happy paths ---------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        {"action": "CLICK", "target": "e2"},
        {"action": "TYPE", "target": "e1", "value": "RTX 4060 laptop"},
        {"action": "SELECT", "target": "e3", "value": "80000"},
        {"action": "SCROLL", "direction": "down"},
        {"action": "NAVIGATE", "value": "https://shop.example.com/deals"},
        {"action": "EXTRACT"},
        {"action": "EXTRACT", "target": "e4"},
        {"action": "WAIT", "timeout_ms": 500},
    ],
)
def test_valid_actions_pass(raw: dict, page: SemanticPage) -> None:
    outcome = validate_action(raw, page)
    assert outcome.valid, outcome.error


def test_scroll_defaults_to_down(page: SemanticPage) -> None:
    outcome = validate_action({"action": "SCROLL"}, page)
    assert outcome.valid
    assert outcome.action.direction == "down"


# --- arbitrary code protection (AC-16) -----------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        {"action": "EVAL", "value": "alert(1)"},
        {"action": "EXECUTE_SCRIPT", "value": "fetch('/')"},
        {"action": "click", "target": "e2", "script": "alert(1)"},
        {"action": "CLICK", "target": "button.buy-now"},
        {"action": "CLICK", "target": "#checkout"},
        {"action": "CLICK", "target": "e2; alert(1)"},
        {"action": "NAVIGATE", "value": "javascript:alert(document.cookie)"},
        {"action": "NAVIGATE", "value": "data:text/html,<script>alert(1)</script>"},
        {"action": "NAVIGATE", "value": "file:///etc/passwd"},
        {"action": "TYPE", "target": "e1", "value": "<script>steal()</script>"},
        {"action": "TYPE", "target": "e1", "value": "javascript:void(0)"},
    ],
)
def test_dangerous_actions_are_rejected(raw: dict, page: SemanticPage) -> None:
    outcome = validate_action(raw, page)
    assert not outcome.valid
    assert outcome.error


def test_extra_fields_are_forbidden(page: SemanticPage) -> None:
    """A model cannot smuggle an unknown field past the schema."""
    outcome = validate_action(
        {"action": "CLICK", "target": "e2", "selector": "body", "js": "alert(1)"}, page
    )
    assert not outcome.valid


def test_target_must_be_a_semantic_id() -> None:
    with pytest.raises(ValidationError, match="semantic element id"):
        BrowserAction(action="CLICK", target="div > button.primary")


# --- target existence and state ------------------------------------------


def test_missing_element_is_rejected_with_a_repair_hint(page: SemanticPage) -> None:
    outcome = validate_action({"action": "CLICK", "target": "e99"}, page)
    assert not outcome.valid
    assert "e99" in outcome.repair_hint
    # The hint lists real ids so the planner can self-correct.
    assert "e1" in outcome.repair_hint


def test_hidden_element_is_rejected(product_page) -> None:
    product_page["elements"][1]["visible"] = False
    page = SemanticPage.model_validate(product_page)
    outcome = validate_action({"action": "CLICK", "target": "e2"}, page)
    assert not outcome.valid
    assert "not visible" in outcome.error


def test_disabled_element_is_rejected(product_page) -> None:
    product_page["elements"][1]["disabled"] = True
    page = SemanticPage.model_validate(product_page)
    outcome = validate_action({"action": "CLICK", "target": "e2"}, page)
    assert not outcome.valid
    assert "disabled" in outcome.error


def test_no_snapshot_means_no_targeted_action() -> None:
    outcome = validate_action({"action": "CLICK", "target": "e1"}, None)
    assert not outcome.valid


# --- type compatibility ---------------------------------------------------


def test_cannot_type_into_a_button(page: SemanticPage) -> None:
    outcome = validate_action({"action": "TYPE", "target": "e2", "value": "x"}, page)
    assert not outcome.valid
    assert "TYPE" in outcome.repair_hint


def test_cannot_select_on_a_non_select(page: SemanticPage) -> None:
    outcome = validate_action({"action": "SELECT", "target": "e1", "value": "x"}, page)
    assert not outcome.valid


def test_select_value_must_be_an_option(page: SemanticPage) -> None:
    outcome = validate_action({"action": "SELECT", "target": "e3", "value": "999999"}, page)
    assert not outcome.valid
    assert "80000" in outcome.repair_hint


# --- required fields ------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        {"action": "CLICK"},
        {"action": "TYPE", "target": "e1"},
        {"action": "SELECT", "target": "e3"},
        {"action": "NAVIGATE"},
    ],
)
def test_missing_required_fields_rejected(raw: dict, page: SemanticPage) -> None:
    assert not validate_action(raw, page).valid


# --- URL rules ------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.com/a", True),
        ("http://localhost:5173/demo", True),
        ("javascript:alert(1)", False),
        ("JAVASCRIPT:alert(1)", False),
        ("data:text/html,x", False),
        ("file:///c:/windows", False),
        ("/relative/path", False),
        ("", False),
        ("https://", False),
    ],
)
def test_url_validation(url: str, expected: bool) -> None:
    assert validate_url(url)[0] is expected


# --- value hygiene --------------------------------------------------------


def test_long_values_are_capped(page: SemanticPage) -> None:
    outcome = validate_action(
        {"action": "TYPE", "target": "e1", "value": "a" * 5000}, page
    )
    # Pydantic's max_length rejects it outright, which is the safe outcome.
    assert not outcome.valid or len(outcome.action.value) <= MAX_VALUE_LENGTH


def test_control_characters_are_stripped(page: SemanticPage) -> None:
    outcome = validate_action(
        {"action": "TYPE", "target": "e1", "value": "laptop\x00\x07 deal"}, page
    )
    assert outcome.valid
    assert "\x00" not in outcome.action.value
    assert "laptop deal" in outcome.action.value
