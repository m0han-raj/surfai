"""Adapting our schemas to what strict structured output accepts.

Strict mode (Groq's constrained decoding, OpenAI's strict json_schema) is worth
reaching: it makes the shape a property of the decoder rather than a request to
the model, which is both one HTTP round trip instead of three and one fewer
thing a hostile page can talk the model out of.

It also refuses most of what our schemas say. These tests pin the translation
and, just as importantly, pin what it refuses to translate: a schema it cannot
express exactly must be declined, never approximated. An approximation here
would silently constrain the model to the wrong shape.
"""

from __future__ import annotations

import copy

from app.agents.memory_agent import FAVOURITE_DRAFT_SCHEMA
from app.agents.planner import INTENT_SCHEMA
from app.browser.action_schema import PLANNER_DECISION_SCHEMA
from app.llm.schemas import strip_optional_nulls, to_strict_schema, validate_against_schema

# --- the translation ------------------------------------------------------


def test_every_property_becomes_required() -> None:
    """The rule strict mode actually enforces, quoted from its own rejection.

    Groq: "`required` is required to be supplied and to be an array including
    every key in properties."
    """
    strict = to_strict_schema(PLANNER_DECISION_SCHEMA)
    assert strict is not None
    assert set(strict["required"]) == set(strict["properties"])


def test_an_optional_property_becomes_nullable() -> None:
    """Optionality has to survive the trip, or the model must invent values."""
    strict = to_strict_schema(PLANNER_DECISION_SCHEMA)
    assert strict is not None

    # `type` was required, so it stays a plain string.
    assert strict["properties"]["type"]["type"] == "string"
    # `activity` was not, so null becomes a legal answer.
    assert strict["properties"]["activity"]["type"] == ["string", "null"]


def test_a_nullable_enum_also_admits_null() -> None:
    """Otherwise the two constraints contradict each other.

    `direction` is optional and enumerated. Making it nullable without adding
    null to the enum leaves the model no legal way to omit it: null fails the
    enum, and any enum value asserts a direction that was never chosen.
    """
    strict = to_strict_schema(PLANNER_DECISION_SCHEMA)
    assert strict is not None
    direction = strict["properties"]["action"]["properties"]["direction"]

    assert direction["type"] == ["string", "null"]
    assert None in direction["enum"]
    assert "down" in direction["enum"]


def test_a_required_enum_is_left_alone() -> None:
    strict = to_strict_schema(PLANNER_DECISION_SCHEMA)
    assert strict is not None
    decision = strict["properties"]["type"]

    assert decision["enum"] == ["action", "answer", "ask"]
    assert None not in decision["enum"]


def test_nested_objects_are_translated_too() -> None:
    """The rejection named a nested path, not the root."""
    strict = to_strict_schema(PLANNER_DECISION_SCHEMA)
    assert strict is not None
    action = strict["properties"]["action"]

    assert set(action["required"]) == set(action["properties"])
    assert action["additionalProperties"] is False
    # And the object itself was optional, so it is nullable.
    assert action["type"] == ["object", "null"]


def test_unsupported_constraints_are_dropped() -> None:
    """Strict mode rejects length and range keywords outright."""
    # The draft schema as a whole is declined for its open `preferences` map
    # (see below); the length caps are what this test is about.
    trimmed = copy.deepcopy(FAVOURITE_DRAFT_SCHEMA)
    del trimmed["properties"]["preferences"]
    strict = to_strict_schema(trimmed)
    assert strict is not None

    for name in ("name", "intent", "description"):
        assert "maxLength" not in strict["properties"][name]


def test_additional_properties_is_forced_closed() -> None:
    strict = to_strict_schema(INTENT_SCHEMA)
    assert strict is not None
    assert strict["additionalProperties"] is False


def test_array_items_are_translated() -> None:
    schema = {
        "type": "object",
        "required": ["rows"],
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["a"],
                    "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
                },
            }
        },
    }
    strict = to_strict_schema(schema)
    assert strict is not None
    items = strict["properties"]["rows"]["items"]

    assert set(items["required"]) == {"a", "b"}
    assert items["properties"]["b"]["type"] == ["string", "null"]


# --- what it refuses to do ------------------------------------------------


def test_a_free_form_object_is_declined_rather_than_guessed_at() -> None:
    """`preferences` is an open map; strict mode has no way to say that.

    Inventing a closed shape for it would constrain the model to fields we made
    up. Declining sends this one schema down the looser path, which is correct
    and costs one request, not a wrong answer.
    """
    free_form = {
        "type": "object",
        "required": ["bag"],
        "properties": {"bag": {"type": "object"}},
    }
    assert to_strict_schema(free_form) is None


def test_the_favourite_draft_schema_is_declined_for_that_reason() -> None:
    """Named explicitly so the reason is discoverable from the test run."""
    assert "preferences" in FAVOURITE_DRAFT_SCHEMA["properties"]
    assert FAVOURITE_DRAFT_SCHEMA["properties"]["preferences"] == {"type": "object"}
    # Drop the open map and the rest of it translates fine.
    trimmed = copy.deepcopy(FAVOURITE_DRAFT_SCHEMA)
    del trimmed["properties"]["preferences"]
    assert to_strict_schema(trimmed) is not None


def test_the_original_schema_is_never_mutated() -> None:
    """These are module-level constants shared by every request in the process.

    Mutating one in place would corrupt it for every later call, and the first
    request would look perfectly fine.
    """
    for schema in (PLANNER_DECISION_SCHEMA, INTENT_SCHEMA, FAVOURITE_DRAFT_SCHEMA):
        before = copy.deepcopy(schema)
        to_strict_schema(schema)
        assert schema == before


# --- coming back ----------------------------------------------------------


def test_nulls_the_model_had_to_emit_are_stripped() -> None:
    """The caller's contract must not change just because the wire did.

    Strict mode forces the model to mention every property, so an omitted one
    arrives as an explicit null. Callers written against the original schema
    expect it absent, and `"activity" in parsed` should not start answering
    True.
    """
    parsed = {"type": "answer", "message": "Paris", "activity": None, "action": None}
    cleaned = strip_optional_nulls(parsed, PLANNER_DECISION_SCHEMA)

    assert cleaned == {"type": "answer", "message": "Paris"}


def test_a_required_null_is_left_for_validation_to_reject() -> None:
    """Stripping it would turn a schema violation into a missing-key mystery."""
    parsed = {"type": None, "message": "hello"}
    cleaned = strip_optional_nulls(parsed, PLANNER_DECISION_SCHEMA)

    assert cleaned["type"] is None
    assert validate_against_schema(cleaned, PLANNER_DECISION_SCHEMA)


def test_nested_nulls_are_stripped_too() -> None:
    parsed = {
        "type": "action",
        "activity": None,
        "message": None,
        "action": {"action": "CLICK", "target": "e1", "value": None, "direction": None,
                   "timeout_ms": None, "reason": None},
    }
    cleaned = strip_optional_nulls(parsed, PLANNER_DECISION_SCHEMA)

    assert cleaned == {"type": "action", "action": {"action": "CLICK", "target": "e1"}}


def test_a_strict_round_trip_still_satisfies_the_original_schema() -> None:
    """The whole point: the looser original stays the gate we actually check."""
    parsed = {"type": "action", "activity": None, "message": None,
              "action": {"action": "TYPE", "target": "e1", "value": "mouse",
                         "direction": None, "timeout_ms": None, "reason": None}}

    cleaned = strip_optional_nulls(parsed, PLANNER_DECISION_SCHEMA)
    assert validate_against_schema(cleaned, PLANNER_DECISION_SCHEMA) == []


def test_length_limits_still_apply_locally_after_being_dropped_on_the_wire() -> None:
    """Dropping `maxLength` for strict mode must not stop us enforcing it."""
    too_long = {"name": "x" * 100, "intent": "buy", "description": "d"}
    errors = validate_against_schema(too_long, FAVOURITE_DRAFT_SCHEMA)

    assert any("maxLength" in error for error in errors)
