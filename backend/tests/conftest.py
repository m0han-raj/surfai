"""Shared test fixtures.

The suite runs against SQLite and a scripted fake LLM, so it needs neither
PostgreSQL nor a model runtime and is safe to run in CI.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("ENVIRONMENT", "test")

from app.config import settings  # noqa: E402
from app.llm.provider import (  # noqa: E402
    LLMProvider,
    LLMResponse,
    Message,
    StructuredOutputError,
)
from app.llm.schemas import validate_against_schema  # noqa: E402


class FakeLLM(LLMProvider):
    """A scripted provider.

    `queue` holds the objects `generate_structured` will return, in order. Each
    is validated against the caller's schema exactly as a real provider would,
    so a test fixture that would break the real contract also fails here.
    """

    def __init__(self, queue: list[Any] | None = None, text: str = "") -> None:
        self.queue: list[Any] = list(queue or [])
        self.text = text
        self.calls: list[dict[str, Any]] = []
        self.unavailable = False

    def push(self, *items: Any) -> None:
        self.queue.extend(items)

    async def generate(self, messages: list[Message], **kwargs: Any) -> LLMResponse:
        self.calls.append({"kind": "generate", "messages": messages})
        if self.unavailable:
            from app.llm.provider import LLMUnavailableError

            raise LLMUnavailableError("fake endpoint is down")
        return LLMResponse(content=self.text, model="fake")

    async def generate_structured(
        self,
        messages: list[Message],
        *,
        schema: dict[str, Any],
        schema_name: str = "response",
        temperature: float | None = None,
        max_retries: int = 1,
    ) -> dict[str, Any]:
        self.calls.append(
            {"kind": "structured", "schema_name": schema_name, "messages": messages}
        )
        if self.unavailable:
            from app.llm.provider import LLMUnavailableError

            raise LLMUnavailableError("fake endpoint is down")
        if not self.queue:
            raise StructuredOutputError(
                f"FakeLLM queue is empty; the code asked for '{schema_name}' "
                "but the test did not script a response"
            )
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        errors = validate_against_schema(item, schema)
        if errors:
            raise StructuredOutputError(
                f"Scripted response for '{schema_name}' does not match the schema: {errors}"
            )
        return item

    async def tool_call(self, messages: list[Message], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"kind": "tool_call", "messages": messages})
        return {"tool_calls": [], "content": ""}

    async def health(self) -> dict[str, Any]:
        return {"reachable": not self.unavailable, "model": "fake", "base_url": "fake://"}

    def prompt_text(self) -> str:
        """Everything ever sent to the model, for injection assertions."""
        parts = []
        for call in self.calls:
            for message in call.get("messages", []):
                parts.append(message.content)
        return "\n".join(parts)


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'surfai-test.db'}"


@pytest.fixture
def database(db_url: str) -> Iterator[None]:
    """A fresh SQLite database per test."""
    from app.database.database import configure_engine, create_all, dispose_engine

    configure_engine(db_url)
    create_all()
    yield
    dispose_engine()


@pytest.fixture
def client(database, fake_llm) -> Iterator[Any]:
    """TestClient with the fake LLM and a per-test orchestrator."""
    from fastapi.testclient import TestClient

    from app.agents.orchestrator import Orchestrator
    from app.api.deps import set_orchestrator
    from app.database.repositories.tasks import DatabaseTaskStore
    from app.llm.openai_compatible import set_provider
    from app.main import app

    set_provider(fake_llm)
    set_orchestrator(Orchestrator(fake_llm, DatabaseTaskStore()))

    with TestClient(app) as test_client:
        yield test_client

    set_provider(None)
    set_orchestrator(None)


# --- page fixtures -------------------------------------------------------

DEMO_PRODUCT_PAGE: dict[str, Any] = {
    "url": "https://shop.example.com/products",
    "domain": "shop.example.com",
    "title": "Product Search",
    "summary": "Browse laptops and accessories. 24 products available.",
    "truncated": 0,
    "elements": [
        {
            "id": "e1",
            "type": "input",
            "tag": "input",
            "inputType": "search",
            "placeholder": "Search products",
            "ariaLabel": "Search products",
            "visible": True,
        },
        {"id": "e2", "type": "button", "tag": "button", "text": "Search", "visible": True},
        {
            "id": "e3",
            "type": "select",
            "tag": "select",
            "ariaLabel": "Maximum price",
            "options": ["Any", "50000", "80000", "120000"],
            "visible": True,
        },
        {
            "id": "e4",
            "type": "link",
            "tag": "a",
            "text": "Lenovo LOQ RTX 4060",
            "href": "https://shop.example.com/product/1",
            "visible": True,
        },
        {"id": "e5", "type": "button", "tag": "button", "text": "Next page", "visible": True},
        {
            "id": "e6",
            "type": "button",
            "tag": "button",
            "text": "Buy now",
            "visible": True,
        },
        {
            "id": "e7",
            "type": "input",
            "tag": "input",
            "inputType": "password",
            "name": "password",
            "ariaLabel": "Password",
            "value": "hunter2",
            "visible": True,
        },
        {"id": "e8", "type": "heading", "tag": "h1", "text": "Products", "level": 1},
    ],
}


@pytest.fixture
def product_page() -> dict[str, Any]:
    return json.loads(json.dumps(DEMO_PRODUCT_PAGE))


@pytest.fixture
def temp_settings() -> Iterator[Any]:
    """Restore settings mutated by a test."""
    original = settings.model_dump()
    yield settings
    for key, value in original.items():
        setattr(settings, key, value)


@pytest.fixture(scope="session", autouse=True)
def _isolate_env() -> Iterator[None]:
    """Keep tests from picking up a developer's real .env values."""
    with tempfile.TemporaryDirectory():
        yield
