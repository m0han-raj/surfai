"""LLM layer: JSON recovery, schema validation, and provider negotiation.

Small local models are messy. These tests pin down exactly how much mess is
recovered and where the line is -- recovery repairs *syntax*, never semantics.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.browser.action_schema import PLANNER_DECISION_SCHEMA
from app.llm.openai_compatible import OpenAICompatibleProvider, _is_unsupported_format
from app.llm.provider import (
    LLMResponseError,
    LLMUnavailableError,
    Message,
    StructuredOutputError,
)
from app.llm.schemas import (
    JSONExtractionError,
    extract_json,
    validate_against_schema,
)

# --- JSON recovery --------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        '{"type":"action"}',
        '```json\n{"type":"action"}\n```',
        '```\n{"type":"action"}\n```',
        'Here is my decision:\n{"type":"action"}\nHope that helps.',
        '{"type":"action",}',
        '<think>The user wants to search.</think>{"type":"action"}',
        '[{"type":"action"}]',
        '  \n {"type":"action"} \n ',
    ],
)
def test_json_is_recovered_from_messy_output(text: str) -> None:
    assert extract_json(text)["type"] == "action"


def test_braces_inside_strings_do_not_confuse_the_parser() -> None:
    parsed = extract_json('{"message": "use the {placeholder} syntax", "type": "answer"}')
    assert parsed["message"] == "use the {placeholder} syntax"


def test_nested_objects_survive() -> None:
    raw = '{"type":"action","action":{"action":"TYPE","target":"e1","value":"a b"}}'
    assert extract_json(raw)["action"]["target"] == "e1"


@pytest.mark.parametrize("text", ["", "   ", "I cannot help with that.", "null", "[]"])
def test_unrecoverable_output_raises(text: str) -> None:
    with pytest.raises(JSONExtractionError):
        extract_json(text)


# --- schema validation ----------------------------------------------------


def test_valid_planner_decisions_pass() -> None:
    for decision in (
        {"type": "action", "action": {"action": "CLICK", "target": "e1"}},
        {"type": "answer", "message": "done"},
        {"type": "ask", "message": "which one?"},
    ):
        assert validate_against_schema(decision, PLANNER_DECISION_SCHEMA) == []


@pytest.mark.parametrize(
    "decision",
    [
        {"type": "execute_js"},
        {"type": "action", "action": {"action": "EVAL"}},
        {"type": "action", "action": {"action": "CLICK", "script": "x"}},
        {"type": "action", "extra_field": 1},
        {},
    ],
)
def test_invalid_planner_decisions_are_reported(decision: dict) -> None:
    assert validate_against_schema(decision, PLANNER_DECISION_SCHEMA)


def test_booleans_are_not_accepted_as_numbers() -> None:
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    assert validate_against_schema({"n": True}, schema)
    assert validate_against_schema({"n": 3}, schema) == []


def test_bounds_and_lengths_are_checked() -> None:
    schema = {
        "type": "object",
        "properties": {
            "n": {"type": "integer", "minimum": 1, "maximum": 10},
            "s": {"type": "string", "maxLength": 3},
        },
    }
    assert validate_against_schema({"n": 0}, schema)
    assert validate_against_schema({"n": 99}, schema)
    assert validate_against_schema({"s": "abcd"}, schema)
    assert validate_against_schema({"n": 5, "s": "abc"}, schema) == []


def test_arrays_are_validated_per_item() -> None:
    schema = {"type": "array", "items": {"type": "string"}}
    assert validate_against_schema(["a", "b"], schema) == []
    assert validate_against_schema(["a", 2], schema)


# --- provider behaviour ---------------------------------------------------


def _provider(handler) -> OpenAICompatibleProvider:
    """Provider wired to an in-memory transport -- no network, no model."""
    provider = OpenAICompatibleProvider(
        base_url="http://llm.test/v1", api_key="", model="test-model"
    )
    provider._client = httpx.AsyncClient(
        base_url="http://llm.test/v1", transport=httpx.MockTransport(handler)
    )
    return provider


def _completion(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "test-model",
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 42},
        },
    )


async def test_generate_returns_content() -> None:
    provider = _provider(lambda request: _completion("hello"))
    response = await provider.generate([Message("user", "hi")])
    assert response.content == "hello"
    assert response.usage["total_tokens"] == 42


async def test_structured_output_uses_json_schema_when_supported() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _completion('{"type":"answer","message":"ok"}')

    provider = _provider(handler)
    parsed = await provider.generate_structured(
        [Message("user", "hi")], schema=PLANNER_DECISION_SCHEMA
    )
    assert parsed["message"] == "ok"
    assert seen[0]["response_format"]["type"] == "json_schema"


async def test_falls_back_when_json_schema_is_unsupported() -> None:
    """Ollama and llama.cpp builds reject strict json_schema; we degrade."""
    modes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        mode = payload.get("response_format", {}).get("type", "prompt")
        modes.append(mode)
        if mode == "json_schema":
            return httpx.Response(
                400, json={"error": {"message": "response_format json_schema not supported"}}
            )
        return _completion('{"type":"answer","message":"ok"}')

    provider = _provider(handler)
    parsed = await provider.generate_structured(
        [Message("user", "hi")], schema=PLANNER_DECISION_SCHEMA
    )
    assert parsed["message"] == "ok"
    assert modes == ["json_schema", "json_object"]


async def test_successful_mode_is_remembered() -> None:
    modes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        mode = payload.get("response_format", {}).get("type", "prompt")
        modes.append(mode)
        if mode == "json_schema":
            return httpx.Response(400, json={"error": {"message": "unsupported"}})
        return _completion('{"type":"answer","message":"ok"}')

    provider = _provider(handler)
    await provider.generate_structured([Message("user", "hi")], schema=PLANNER_DECISION_SCHEMA)
    modes.clear()
    await provider.generate_structured([Message("user", "hi")], schema=PLANNER_DECISION_SCHEMA)
    assert modes == ["json_object"], "the rejected mode should not be retried"


async def test_invalid_output_triggers_one_constrained_retry_then_fails() -> None:
    """AC-26: reject, retry once with the errors, then fail safely."""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return _completion("I'd rather explain this in prose.")

    provider = _provider(handler)
    with pytest.raises(StructuredOutputError) as exc:
        await provider.generate_structured(
            [Message("user", "hi")], schema=PLANNER_DECISION_SCHEMA, max_retries=1
        )
    assert exc.value.attempts == 2
    # The retry tells the model exactly what was wrong.
    last = bodies[-1]["messages"][-1]["content"]
    assert "single JSON object" in last


async def test_schema_violating_output_is_never_returned() -> None:
    provider = _provider(lambda request: _completion('{"type":"execute_javascript"}'))
    with pytest.raises(StructuredOutputError):
        await provider.generate_structured(
            [Message("user", "hi")], schema=PLANNER_DECISION_SCHEMA, max_retries=0
        )


async def test_connection_failure_is_reported_as_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = _provider(handler)
    with pytest.raises(LLMUnavailableError) as exc:
        await provider.generate([Message("user", "hi")])
    assert "llm.test" in str(exc.value)


async def test_timeout_is_reported_as_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    provider = _provider(handler)
    with pytest.raises(LLMUnavailableError):
        await provider.generate([Message("user", "hi")])


async def test_bad_credentials_produce_an_actionable_message() -> None:
    provider = _provider(lambda request: httpx.Response(401, json={"error": "nope"}))
    with pytest.raises(LLMResponseError) as exc:
        await provider.generate([Message("user", "hi")])
    assert "LLM_API_KEY" in str(exc.value)


async def test_api_key_is_sent_as_a_bearer_token() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return _completion("ok")

    provider = OpenAICompatibleProvider(
        base_url="http://llm.test/v1", api_key="secret-key", model="m"
    )
    provider._client = httpx.AsyncClient(
        base_url="http://llm.test/v1",
        transport=httpx.MockTransport(handler),
        headers=provider._headers(),
    )
    await provider.generate([Message("user", "hi")])
    assert seen[0] == "Bearer secret-key"


async def test_no_auth_header_when_no_key_is_configured() -> None:
    provider = OpenAICompatibleProvider(base_url="http://llm.test/v1", api_key="", model="m")
    assert "Authorization" not in provider._headers()


async def test_health_probe_reports_model_availability() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(200, json={"data": [{"id": "test-model"}, {"id": "other"}]})

    provider = _provider(handler)
    info = await provider.health()
    assert info["reachable"] is True
    assert info["model_available"] is True


async def test_health_probe_handles_an_unreachable_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    provider = _provider(handler)
    info = await provider.health()
    assert info["reachable"] is False
    assert info["error"]


async def test_tool_calling_parses_native_responses() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "function": {
                                        "name": "search",
                                        "arguments": '{"query":"laptops"}',
                                    }
                                }
                            ],
                        }
                    }
                ]
            },
        )

    provider = _provider(handler)
    result = await provider.tool_call([Message("user", "hi")], tools=[])
    assert result["tool_calls"][0]["name"] == "search"
    assert result["tool_calls"][0]["arguments"]["query"] == "laptops"


@pytest.mark.parametrize(
    "message,expected",
    [
        ("LLM returned HTTP 400: response_format not supported", True),
        ("unknown parameter: tools", True),
        ("LLM returned HTTP 500: internal server error", False),
    ],
)
def test_unsupported_format_detection(message: str, expected: bool) -> None:
    assert _is_unsupported_format(message) is expected
