"""Provider-agnostic LLM interface.

SurfAI never imports a vendor SDK. Everything goes through `LLMProvider`, so a
local Ollama build, a llama.cpp server, vLLM or a hosted OpenAI-compatible
endpoint are interchangeable through configuration alone.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    """One chat turn.

    `role` is restricted to the three standard roles; untrusted webpage content
    is never given its own role -- it is embedded as data inside a user turn.
    """

    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class LLMResponse:
    content: str
    model: str = ""
    finish_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


class LLMError(RuntimeError):
    """Base class for provider failures."""


class LLMUnavailableError(LLMError):
    """The endpoint could not be reached or timed out."""


class LLMResponseError(LLMError):
    """The endpoint replied, but not with something usable."""


class StructuredOutputError(LLMError):
    """The model's output did not satisfy the requested schema."""

    def __init__(self, message: str, *, raw_output: str = "", attempts: int = 0) -> None:
        super().__init__(message)
        self.raw_output = raw_output
        self.attempts = attempts


class LLMProvider(abc.ABC):
    """Interface implemented by every provider."""

    @abc.abstractmethod
    async def generate(
        self,
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        """Free-form completion."""

    @abc.abstractmethod
    async def generate_structured(
        self,
        messages: list[Message],
        *,
        schema: dict[str, Any],
        schema_name: str = "response",
        temperature: float | None = None,
        max_retries: int = 1,
    ) -> dict[str, Any]:
        """Completion constrained to a JSON Schema.

        Implementations must validate the parsed object against the schema and
        raise `StructuredOutputError` rather than returning something unchecked.
        """

    @abc.abstractmethod
    async def tool_call(
        self,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]],
        tool_choice: str = "auto",
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """Tool-calling completion.

        Returns ``{"tool_calls": [...], "content": str}``. Providers without
        native tool support emulate it via structured output.
        """

    @abc.abstractmethod
    async def health(self) -> dict[str, Any]:
        """Report reachability and the configured model."""

    async def aclose(self) -> None:
        """Release any held connections.

        Concrete, not abstract: a stateless provider has nothing to close, so
        requiring an override would be noise.
        """
        return None
