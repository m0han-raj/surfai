"""What the provider actually puts on the wire for structured output.

Both of these were found by running real requests against Groq, and both cost
two wasted round trips per decision before they were fixed. On a free tier
allowing ten requests a minute, a fifteen-step agent task spending three
requests per step does not finish.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.browser.action_schema import PLANNER_DECISION_SCHEMA
from app.llm.openai_compatible import OpenAICompatibleProvider
from app.llm.provider import Message

SIMPLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["answer"],
    "additionalProperties": False,
    "properties": {"answer": {"type": "string"}, "note": {"type": "string"}},
}


class Recorder:
    """Stands in for the endpoint and keeps every payload it was sent."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.payloads: list[dict[str, Any]] = []

    async def __call__(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        return {"choices": [{"message": {"content": self.reply}}], "model": "test"}

    @property
    def modes(self) -> list[str]:
        return [(p.get("response_format") or {}).get("type", "none") for p in self.payloads]


@pytest.fixture
def provider(monkeypatch) -> OpenAICompatibleProvider:
    monkeypatch.setenv("LLM_BASE_URL", "https://example.invalid/v1")
    return OpenAICompatibleProvider(base_url="https://example.invalid/v1", model="m")


# --- strict schema on the wire --------------------------------------------


@pytest.mark.asyncio
async def test_the_schema_sent_is_the_strict_translation(provider, monkeypatch) -> None:
    """Not the original, which strict endpoints reject with a 400."""
    recorder = Recorder(json.dumps({"answer": "yes", "note": None}))
    monkeypatch.setattr(provider, "_post", recorder)

    await provider.generate_structured(
        [Message(role="user", content="go")], schema=SIMPLE_SCHEMA
    )

    sent = recorder.payloads[0]["response_format"]["json_schema"]["schema"]
    assert set(sent["required"]) == {"answer", "note"}
    assert sent["properties"]["note"]["type"] == ["string", "null"]


@pytest.mark.asyncio
async def test_one_request_is_enough_when_strict_is_accepted(provider, monkeypatch) -> None:
    recorder = Recorder(json.dumps({"answer": "yes", "note": None}))
    monkeypatch.setattr(provider, "_post", recorder)

    await provider.generate_structured(
        [Message(role="user", content="go")], schema=SIMPLE_SCHEMA
    )

    assert recorder.modes == ["json_schema"], "it spent more than one round trip"


@pytest.mark.asyncio
async def test_the_forced_nulls_do_not_reach_the_caller(provider, monkeypatch) -> None:
    """Strict mode makes the model name every property; callers expect absence."""
    recorder = Recorder(json.dumps({"answer": "yes", "note": None}))
    monkeypatch.setattr(provider, "_post", recorder)

    parsed = await provider.generate_structured(
        [Message(role="user", content="go")], schema=SIMPLE_SCHEMA
    )

    assert parsed == {"answer": "yes"}
    assert "note" not in parsed


@pytest.mark.asyncio
async def test_a_schema_with_no_strict_equivalent_skips_that_mode(provider, monkeypatch) -> None:
    """Sending an approximation would constrain the model to the wrong shape."""
    open_map = {
        "type": "object",
        "required": ["bag"],
        "properties": {"bag": {"type": "object"}},
    }
    recorder = Recorder(json.dumps({"bag": {"anything": 1}}))
    monkeypatch.setattr(provider, "_post", recorder)

    await provider.generate_structured([Message(role="user", content="go")], schema=open_map)

    assert "json_schema" not in recorder.modes
    assert recorder.modes[0] == "json_object"


# --- the json_object precondition -----------------------------------------


@pytest.mark.asyncio
async def test_json_object_mode_says_the_word_json(provider, monkeypatch) -> None:
    """Groq and OpenAI both 400 without it.

    Groq: "'messages' must contain the word 'json' in some form, to use
    'response_format' of type 'json_object'." Our planner prompts never say it,
    so this mode failed outright and every call fell to prompt-only extraction.
    """
    recorder = Recorder(json.dumps({"bag": {}}))
    monkeypatch.setattr(provider, "_post", recorder)
    open_map = {"type": "object", "required": ["bag"],
                "properties": {"bag": {"type": "object"}}}

    await provider.generate_structured([Message(role="user", content="go")], schema=open_map)

    payload = next(p for p in recorder.payloads
                   if (p.get("response_format") or {}).get("type") == "json_object")
    assert any("json" in m["content"].lower() for m in payload["messages"])


@pytest.mark.asyncio
async def test_the_loose_modes_are_told_the_shape(provider, monkeypatch) -> None:
    """`json_object` guarantees syntax, not shape, and `prompt` guarantees neither.

    Against live Groq without this, the model answered a favourite-draft
    request with `title` and `summary` -- valid JSON, wrong fields -- and the
    call only succeeded on the corrective retry, four round trips in.
    """
    recorder = Recorder(json.dumps({"bag": {}}))
    monkeypatch.setattr(provider, "_post", recorder)
    open_map = {"type": "object", "required": ["bag"],
                "properties": {"bag": {"type": "object"}}}

    await provider.generate_structured([Message(role="user", content="go")], schema=open_map)

    for payload in recorder.payloads:
        assert (payload.get("response_format") or {}).get("type") != "json_schema"
        appended = payload["messages"][-1]["content"]
        assert "bag" in appended, "the loose modes were not told which fields to produce"


@pytest.mark.asyncio
async def test_a_loose_mode_costs_one_request_when_the_model_complies(
    provider, monkeypatch
) -> None:
    """Being told the shape up front is what makes the first attempt enough."""
    recorder = Recorder(json.dumps({"bag": {"any": 1}}))
    monkeypatch.setattr(provider, "_post", recorder)
    open_map = {"type": "object", "required": ["bag"],
                "properties": {"bag": {"type": "object"}}}

    await provider.generate_structured([Message(role="user", content="go")], schema=open_map)

    assert len(recorder.payloads) == 1


@pytest.mark.asyncio
async def test_the_caller_s_own_messages_are_left_intact(provider, monkeypatch) -> None:
    """The nudge is appended, never substituted for what the caller wrote."""
    recorder = Recorder(json.dumps({"bag": {}}))
    monkeypatch.setattr(provider, "_post", recorder)
    open_map = {"type": "object", "required": ["bag"],
                "properties": {"bag": {"type": "object"}}}

    await provider.generate_structured(
        [Message(role="system", content="You are careful."),
         Message(role="user", content="go")],
        schema=open_map,
    )

    payload = next(p for p in recorder.payloads
                   if (p.get("response_format") or {}).get("type") == "json_object")
    contents = [m["content"] for m in payload["messages"]]
    assert "You are careful." in contents
    assert "go" in contents


@pytest.mark.asyncio
async def test_strict_mode_does_not_get_the_nudge(provider, monkeypatch) -> None:
    """Constrained decoding needs no instruction, and tokens are not free."""
    recorder = Recorder(json.dumps({"answer": "yes", "note": None}))
    monkeypatch.setattr(provider, "_post", recorder)

    await provider.generate_structured(
        [Message(role="user", content="go")], schema=SIMPLE_SCHEMA
    )

    assert [m["content"] for m in recorder.payloads[0]["messages"]] == ["go"]


# --- the real planner schema ----------------------------------------------


@pytest.mark.asyncio
async def test_the_planner_schema_now_survives_strict_mode(provider, monkeypatch) -> None:
    """The schema whose rejection started this."""
    recorder = Recorder(json.dumps({
        "type": "action", "activity": None, "message": None,
        "action": {"action": "TYPE", "target": "e1", "value": "mouse",
                   "direction": None, "timeout_ms": None, "reason": None},
    }))
    monkeypatch.setattr(provider, "_post", recorder)

    parsed = await provider.generate_structured(
        [Message(role="user", content="go")], schema=PLANNER_DECISION_SCHEMA
    )

    assert recorder.modes == ["json_schema"]
    assert parsed == {"type": "action",
                      "action": {"action": "TYPE", "target": "e1", "value": "mouse"}}
