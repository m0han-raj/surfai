"""OpenAI-compatible chat-completions provider.

Works against any endpoint exposing `/chat/completions`: Ollama, llama.cpp
server, vLLM, LM Studio, OpenRouter, OpenAI itself. Structured output is
negotiated in decreasing order of strictness so that a strict server enforces
the schema, while a small local model still produces usable JSON:

1. ``response_format: json_schema`` (strict grammar, best),
2. ``response_format: json_object`` (valid JSON, unconstrained shape),
3. prompt-only instruction plus local extraction (last resort).

Whichever path produced the text, the object is validated locally before it is
returned. Unvalidated model output never leaves this module.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx

from app.config import settings
from app.llm.provider import (
    LLMProvider,
    LLMResponse,
    LLMResponseError,
    LLMUnavailableError,
    Message,
    StructuredOutputError,
)
from app.llm.schemas import (
    JSONExtractionError,
    extract_json,
    strip_optional_nulls,
    to_strict_schema,
    validate_against_schema,
)

logger = logging.getLogger(__name__)

#: How many times to wait out a rate limit before giving up.
MAX_RATE_LIMIT_RETRIES = 3

#: Longest single wait. A free tier prices tokens per minute and refills, so a
#: few seconds is worth waiting; beyond a minute the user is better told.
MAX_RETRY_WAIT_S = 60.0

#: Groq states the delay in prose rather than only in a header:
#: "Please try again in 5.225s." or "in 1m30s."
_RETRY_IN = re.compile(
    r"try again in\s+(?:(\d+)m)?\s*([\d.]+)s", re.IGNORECASE
)


def retry_after_seconds(body: str, headers: dict) -> float:
    """How long to wait before trying again.

    Prefers the `retry-after` header, falls back to the sentence in the body,
    and otherwise picks a sane default rather than hammering the endpoint.
    """
    header = headers.get("retry-after") or headers.get("Retry-After")
    if header:
        try:
            return min(float(header), MAX_RETRY_WAIT_S)
        except (TypeError, ValueError):
            pass

    match = _RETRY_IN.search(body or "")
    if match:
        minutes = float(match.group(1) or 0)
        seconds = float(match.group(2) or 0)
        # A pause the user is waiting through, so it is capped rather than
        # obeyed literally: an hour is not a wait, it is a failure.
        return min(minutes * 60 + seconds, MAX_RETRY_WAIT_S)

    return 10.0


def _shape_instruction(schema: dict[str, Any]) -> str:
    """Tell a non-strict endpoint what to produce.

    `json_schema` needs none of this: the decoder enforces the shape. The other
    two modes guarantee less and have to be asked. `json_object` promises only
    that the bytes parse, so without the schema the model picks its own field
    names -- live Groq answered a favourite-draft request with `title` and
    `summary`, perfectly valid JSON and entirely the wrong object. `prompt`
    mode promises nothing at all.

    Saying "JSON" is also a hard precondition, not a stylistic choice: Groq and
    OpenAI both reject `json_object` with "'messages' must contain the word
    'json' in some form" otherwise, and none of our prompts say it.
    """
    return (
        "Reply with a single JSON object matching this schema, and nothing else. "
        "No prose, no code fences.\n"
        f"{json.dumps(schema)}"
    )


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.model = model or settings.llm_model
        self.timeout = timeout or settings.llm_timeout_s
        self._client: httpx.AsyncClient | None = None
        # Remembers what this endpoint supports so we stop paying for
        # rejected requests after the first probe.
        self._structured_mode: str | None = None

    # -- plumbing ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                headers=self._headers(),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def _post(
        self, path: str, payload: dict[str, Any], attempt: int = 0
    ) -> dict[str, Any]:
        client = await self._get_client()
        try:
            response = await client.post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise LLMUnavailableError(
                f"LLM request timed out after {self.timeout}s at {self.base_url}"
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"Could not reach the LLM at {self.base_url}: {exc}"
            ) from exc

        if response.status_code >= 400:
            body = response.text[:500]
            if response.status_code in (401, 403):
                raise LLMResponseError(
                    f"LLM rejected the credentials (HTTP {response.status_code}). "
                    "Check LLM_API_KEY."
                )
            if response.status_code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                # A free tier prices tokens per minute and the budget refills,
                # so this is a pause rather than a failure. A five-step task
                # costs more than a minute's worth on every free tier there is;
                # giving up at the moment it runs out throws away a task that
                # would have finished a few seconds later.
                delay = retry_after_seconds(body, dict(response.headers))
                logger.info(
                    "Rate limited by the LLM; waiting %.1fs (attempt %d of %d)",
                    delay,
                    attempt + 1,
                    MAX_RATE_LIMIT_RETRIES,
                )
                await asyncio.sleep(delay)
                return await self._post(path, payload, attempt=attempt + 1)
            raise LLMResponseError(f"LLM returned HTTP {response.status_code}: {body}")

        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise LLMResponseError("LLM returned a non-JSON body") from exc

    @staticmethod
    def _first_message(data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices") or []
        if not choices:
            raise LLMResponseError("LLM response contained no choices")
        return choices[0].get("message") or {}

    def _payload(
        self,
        messages: list[Message],
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or settings.llm_max_tokens,
            "stream": False,
        }

    # -- LLMProvider ------------------------------------------------------

    async def generate(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        payload = self._payload(messages, temperature, max_tokens)
        if stop:
            payload["stop"] = stop
        data = await self._post("/chat/completions", payload)
        message = self._first_message(data)
        return LLMResponse(
            content=(message.get("content") or "").strip(),
            model=data.get("model", self.model),
            finish_reason=(data.get("choices") or [{}])[0].get("finish_reason"),
            usage=data.get("usage") or {},
            raw=data,
        )

    async def generate_structured(
        self,
        messages: list[Message],
        *,
        schema: dict[str, Any],
        schema_name: str = "response",
        temperature: float | None = None,
        max_retries: int = 1,
    ) -> dict[str, Any]:
        modes = self._modes_to_try()
        last_error = ""
        last_raw = ""
        attempt_messages = list(messages)

        # Strict mode accepts a much smaller dialect than we write in. A schema
        # with no exact equivalent is declined rather than approximated, and
        # simply skips this mode.
        strict_schema = to_strict_schema(schema)
        if strict_schema is None and "json_schema" in modes:
            logger.debug("%s has no strict equivalent; skipping constrained decoding", schema_name)
            modes = [mode for mode in modes if mode != "json_schema"]

        for attempt in range(max_retries + 1):
            for mode in modes:
                messages_for_mode = attempt_messages
                if mode != "json_schema":
                    messages_for_mode = [
                        *attempt_messages,
                        Message(role="user", content=_shape_instruction(schema)),
                    ]

                payload = self._payload(messages_for_mode, temperature, None)
                if mode == "json_schema":
                    payload["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema_name,
                            "strict": True,
                            "schema": strict_schema,
                        },
                    }
                elif mode == "json_object":
                    payload["response_format"] = {"type": "json_object"}

                try:
                    data = await self._post("/chat/completions", payload)
                except LLMResponseError as exc:
                    # A server that does not understand this response_format
                    # answers 400; fall through to the next, looser mode.
                    if mode != "prompt" and _is_unsupported_format(str(exc)):
                        logger.info("Endpoint rejected %s mode; falling back", mode)
                        continue
                    raise

                content = (self._first_message(data).get("content") or "").strip()
                last_raw = content

                try:
                    parsed = extract_json(content)
                except JSONExtractionError as exc:
                    last_error = str(exc)
                    continue

                # Strict mode makes the model name every property, so one it
                # chose to omit arrives as an explicit null. Callers were
                # written against the original schema and expect it absent.
                parsed = strip_optional_nulls(parsed, schema)

                errors = validate_against_schema(parsed, schema)
                if not errors:
                    self._structured_mode = mode
                    return parsed

                last_error = "; ".join(errors[:5])
                logger.info("Structured output failed validation: %s", last_error)

            if attempt < max_retries:
                # One constrained retry: show the model exactly what was wrong.
                attempt_messages = [
                    *messages,
                    Message(role="assistant", content=last_raw[:1500]),
                    Message(
                        role="user",
                        content=(
                            "That response was not valid. Errors:\n"
                            f"{last_error}\n\n"
                            "Reply with a single JSON object matching this schema and "
                            "nothing else -- no prose, no code fences:\n"
                            f"{json.dumps(schema)}"
                        ),
                    ),
                ]

        raise StructuredOutputError(
            f"Model did not produce valid structured output: {last_error}",
            raw_output=last_raw,
            attempts=max_retries + 1,
        )

    def _modes_to_try(self) -> list[str]:
        if self._structured_mode:
            # Keep a looser fallback in case the server changes behaviour.
            order = ["json_schema", "json_object", "prompt"]
            idx = order.index(self._structured_mode)
            return order[idx:]
        return ["json_schema", "json_object", "prompt"]

    async def tool_call(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
        temperature: float | None = None,
    ) -> dict[str, Any]:
        payload = self._payload(messages, temperature, None)
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice

        try:
            data = await self._post("/chat/completions", payload)
        except LLMResponseError as exc:
            if not _is_unsupported_format(str(exc)):
                raise
            # Emulate tool calling for endpoints without native support.
            return await self._emulate_tool_call(messages, tools, temperature)

        message = self._first_message(data)
        calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                arguments = {}
            calls.append({"name": function.get("name", ""), "arguments": arguments})

        return {"tool_calls": calls, "content": (message.get("content") or "").strip()}

    async def _emulate_tool_call(
        self,
        messages: list[Message],
        tools: list[dict[str, Any]],
        temperature: float | None,
    ) -> dict[str, Any]:
        catalogue = json.dumps(
            [
                {
                    "name": t.get("function", {}).get("name"),
                    "description": t.get("function", {}).get("description"),
                    "parameters": t.get("function", {}).get("parameters"),
                }
                for t in tools
            ],
            indent=2,
        )
        schema = {
            "type": "object",
            "required": ["name", "arguments"],
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "arguments": {"type": "object"},
            },
        }
        prompt = [
            *messages,
            Message(
                role="user",
                content=(
                    "Select exactly one tool to call from this catalogue:\n"
                    f"{catalogue}\n\n"
                    'Reply with a single JSON object: {"name": ..., "arguments": {...}}'
                ),
            ),
        ]
        parsed = await self.generate_structured(
            prompt, schema=schema, schema_name="tool_call", temperature=temperature
        )
        return {
            "tool_calls": [{"name": parsed["name"], "arguments": parsed.get("arguments", {})}],
            "content": "",
        }

    async def health(self) -> dict[str, Any]:
        """Probe the endpoint without spending a full generation."""
        info: dict[str, Any] = {
            "base_url": self.base_url,
            "model": self.model,
            "api_key_configured": bool(self.api_key),
            "reachable": False,
            "model_available": None,
            "error": None,
        }
        try:
            client = await self._get_client()
            response = await asyncio.wait_for(client.get("/models"), timeout=10.0)
            if response.status_code < 400:
                info["reachable"] = True
                body = response.json()
                ids = [m.get("id", "") for m in body.get("data", []) if isinstance(m, dict)]
                if ids:
                    info["model_available"] = any(
                        self.model == mid or mid.startswith(f"{self.model}:") for mid in ids
                    )
                    info["available_models"] = ids[:25]
            else:
                info["error"] = f"HTTP {response.status_code} from /models"
        except (TimeoutError, httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
            info["error"] = f"{type(exc).__name__}: {exc}"
        return info


def _is_unsupported_format(error_text: str) -> bool:
    """Heuristic: does this error mean 'I do not support that parameter'?"""
    lowered = error_text.lower()
    markers = (
        "response_format",
        "json_schema",
        "not supported",
        "unsupported",
        "unknown parameter",
        "unrecognized",
        "invalid_request_error",
        "tools",
        "tool_choice",
        "http 400",
        "http 422",
        "http 501",
    )
    return any(m in lowered for m in markers)


_provider: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """Process-wide provider singleton (reuses the HTTP connection pool)."""
    global _provider
    if _provider is None:
        _provider = OpenAICompatibleProvider()
    return _provider


def set_provider(provider: LLMProvider | None) -> None:
    """Override the singleton. Used by tests and by future model routing."""
    global _provider
    _provider = provider
