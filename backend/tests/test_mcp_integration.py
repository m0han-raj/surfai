"""Against a real Playwright MCP server, driving a real browser.

Skipped unless one is running, because it needs Node, a downloaded Chromium
and several seconds per test. Start one with:

    npx @playwright/mcp --port 8931 --headless --isolated --browser chrome
    MCP_TEST_URL=http://localhost:8931/mcp python -m pytest tests/test_mcp_integration.py

Nothing here is mocked. The unit tests pin the protocol against a fake
transport; these exist because a protocol implemented against a specification
is not the same as one implemented against the server, and the differences
were real: the server answers in SSE framing even for a single reply,
`browser_find` takes `text` rather than `query`, `browser_click` takes
`target` rather than `ref`, and a dropped session header silently resets the
browser to about:blank.
"""

from __future__ import annotations

import os
import re

import pytest

from app.mcp.client import McpClient
from app.mcp.transport import HttpTransport

MCP_URL = os.environ.get("MCP_TEST_URL")

pytestmark = pytest.mark.skipif(
    not MCP_URL, reason="set MCP_TEST_URL to run against a live Playwright MCP server"
)


@pytest.fixture
async def client():
    transport = HttpTransport(MCP_URL, timeout=120.0)
    yield McpClient(transport)
    await transport.aclose()


def url_in(text: str) -> str:
    match = re.search(r"Page URL: (\S+)", text)
    return match.group(1) if match else ""


@pytest.mark.asyncio
async def test_it_connects_and_the_server_offers_browser_tools(client) -> None:
    tools = await client.list_tools()
    names = {tool.name for tool in tools}

    assert len(tools) > 10, "a real Playwright MCP server offers a couple of dozen tools"
    # The handful the agent actually depends on.
    assert {"browser_navigate", "browser_snapshot", "browser_click"} <= names


@pytest.mark.asyncio
async def test_every_tool_carries_a_schema_the_model_can_be_given(client) -> None:
    for tool in await client.list_tools():
        as_function = tool.to_openai_tool()
        assert as_function["function"]["name"]
        assert isinstance(as_function["function"]["parameters"], dict)


@pytest.mark.asyncio
async def test_it_actually_navigates_a_browser(client) -> None:
    result = await client.call("browser_navigate", {"url": "https://example.com/"})

    assert result.ok, result.text
    assert url_in(result.text) == "https://example.com/"


@pytest.mark.asyncio
async def test_the_snapshot_is_an_accessibility_tree_with_references(client) -> None:
    """Which is what lets the model name an element without writing a selector."""
    await client.call("browser_navigate", {"url": "https://example.com/"})
    result = await client.call("browser_snapshot", {})

    assert result.ok, result.text
    assert "Example Domain" in result.text
    assert re.search(r"\[ref=e\d+\]", result.text), "no element references in the snapshot"


@pytest.mark.asyncio
async def test_one_client_keeps_one_browser_across_calls(client) -> None:
    """The session header is doing its job.

    Without it the second call runs in a fresh context and reports
    about:blank, silently undoing the navigation before it.
    """
    await client.call("browser_navigate", {"url": "https://example.com/"})
    result = await client.call("browser_snapshot", {})

    assert url_in(result.text) == "https://example.com/"


@pytest.mark.asyncio
async def test_it_clicks_a_real_link_and_the_page_changes(client) -> None:
    await client.call("browser_navigate", {"url": "https://example.com/"})
    snapshot = await client.call("browser_snapshot", {})

    ref = re.search(r'link[^\n]*\[ref=(e\d+)\]', snapshot.text)
    assert ref, f"no link found on example.com: {snapshot.text[:400]}"

    result = await client.call(
        "browser_click", {"element": "the link on the page", "target": ref.group(1)}
    )
    assert result.ok, result.text


@pytest.mark.asyncio
async def test_it_goes_back(client) -> None:
    await client.call("browser_navigate", {"url": "https://example.com/"})
    await client.call("browser_navigate", {"url": "https://example.org/"})

    result = await client.call("browser_navigate_back", {})

    assert result.ok, result.text
    assert url_in(result.text) == "https://example.com/"


@pytest.mark.asyncio
async def test_it_scrolls(client) -> None:
    await client.call("browser_navigate", {"url": "https://example.com/"})
    result = await client.call("browser_press_key", {"key": "End"})

    assert result.ok, result.text


# --- what happens when things go wrong ------------------------------------


@pytest.mark.asyncio
async def test_a_missing_element_is_a_reported_failure_not_an_exception(client) -> None:
    """The agent should read this and try something else."""
    await client.call("browser_navigate", {"url": "https://example.com/"})

    result = await client.call(
        "browser_click", {"element": "a button that is not there", "target": "e99999"}
    )

    assert result.ok is False
    assert result.text


@pytest.mark.asyncio
async def test_a_navigation_that_cannot_resolve_fails_cleanly(client) -> None:
    result = await client.call(
        "browser_navigate", {"url": "https://this-domain-does-not-exist.invalid/"}
    )

    assert result.ok is False
    assert result.text


@pytest.mark.asyncio
async def test_a_tool_the_server_does_not_have_fails_cleanly(client) -> None:
    result = await client.call("browser_do_something_imaginary", {})

    assert result.ok is False


@pytest.mark.asyncio
async def test_a_server_that_is_not_there_is_reported_not_raised() -> None:
    """Mid-task the server can be closed; that must read as a failed step."""
    transport = HttpTransport("http://localhost:9/mcp", timeout=3.0)
    try:
        result = await McpClient(transport).call("browser_snapshot", {})
        assert result.ok is False
        assert result.text
    finally:
        await transport.aclose()
