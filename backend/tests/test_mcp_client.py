"""The MCP client: SurfAI talking to a Playwright MCP server.

Hand-rolled over Streamable HTTP rather than built on the official SDK, for a
measured reason: the SDK and its dependencies add 28MB to a deployment with a
50MB ceiling that is already at 47MB. The protocol we need is small -- an
initialize handshake, a notification, tools/list and tools/call -- and it was
verified against the real `@playwright/mcp` server before a line of this was
written.

What these tests pin is mostly failure. A browser automation server is a
separate process that can be absent, slow, or newly restarted, and none of
those may take down a chat turn.
"""

from __future__ import annotations

import json

import pytest

from app.mcp.client import McpClient, McpError, McpTool, parse_response


class FakeTransport:
    """Stands in for the HTTP round trip, recording what was sent."""

    def __init__(self, *responses, session_id: str = "sess-1") -> None:
        self.queued = list(responses)
        self.sent: list[dict] = []
        self.session_id = session_id
        self.headers_seen: list[dict] = []

    async def post(self, body: dict, headers: dict) -> tuple[str, str | None]:
        self.sent.append(body)
        self.headers_seen.append(dict(headers))
        if body.get("method", "").startswith("notifications/"):
            return "", self.session_id
        if not self.queued:
            raise AssertionError(f"no response queued for {body.get('method')}")
        item = self.queued.pop(0)
        if isinstance(item, Exception):
            raise item
        return json.dumps(item), self.session_id


def ok(result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": 1, "result": result}


HANDSHAKE = ok({"protocolVersion": "2025-06-18", "serverInfo": {"name": "Playwright"}})

TOOLS = ok(
    {
        "tools": [
            {
                "name": "browser_navigate",
                "description": "Navigate to a URL",
                "inputSchema": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            },
            {
                "name": "browser_click",
                "description": "Click an element",
                "inputSchema": {
                    "type": "object",
                    "properties": {"element": {"type": "string"}, "target": {"type": "string"}},
                    "required": ["target"],
                },
            },
        ]
    }
)


# --- the handshake --------------------------------------------------------


@pytest.mark.asyncio
async def test_it_connects_and_discovers_tools() -> None:
    transport = FakeTransport(HANDSHAKE, TOOLS)
    client = McpClient(transport)

    tools = await client.list_tools()

    assert [tool.name for tool in tools] == ["browser_navigate", "browser_click"]
    assert [call["method"] for call in transport.sent] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]


@pytest.mark.asyncio
async def test_the_tool_list_is_whatever_the_server_says() -> None:
    """Never a hard-coded list.

    `browser_find` takes `text`, not `query`, and `browser_click` takes
    `target`, not `ref` -- both of which I got wrong guessing before asking the
    real server. The schema has to come from the server or the model will be
    told to call tools that do not exist in the shape described.
    """
    transport = FakeTransport(HANDSHAKE, ok({"tools": [
        {"name": "some_future_tool", "description": "d", "inputSchema": {"type": "object"}},
    ]}))

    tools = await McpClient(transport).list_tools()
    assert [tool.name for tool in tools] == ["some_future_tool"]


@pytest.mark.asyncio
async def test_the_session_id_is_carried_on_every_later_request() -> None:
    """One session is one browser context.

    Verified against the real server: a second session opens a fresh context,
    so the page a previous call navigated to is gone and the next tool runs
    against about:blank. Dropping the header silently resets the browser
    between steps of the same task.
    """
    transport = FakeTransport(HANDSHAKE, TOOLS)
    client = McpClient(transport)
    await client.list_tools()

    later = transport.headers_seen[-1]
    assert later.get("Mcp-Session-Id") == "sess-1"


@pytest.mark.asyncio
async def test_it_handshakes_once_however_many_calls_follow() -> None:
    transport = FakeTransport(HANDSHAKE, TOOLS, ok({"content": []}), ok({"content": []}))
    client = McpClient(transport)

    await client.list_tools()
    await client.call("browser_navigate", {"url": "https://example.com"})
    await client.call("browser_navigate", {"url": "https://example.org"})

    assert [c["method"] for c in transport.sent].count("initialize") == 1


# --- calling a tool -------------------------------------------------------


@pytest.mark.asyncio
async def test_a_tool_result_comes_back_as_text() -> None:
    transport = FakeTransport(
        HANDSHAKE,
        ok({"content": [{"type": "text", "text": "### Page\n- Page URL: https://example.com/"}]}),
    )
    client = McpClient(transport)

    result = await client.call("browser_navigate", {"url": "https://example.com"})

    assert result.ok is True
    assert "example.com" in result.text


@pytest.mark.asyncio
async def test_a_tool_error_is_reported_rather_than_raised() -> None:
    """The agent should read the failure and try something else.

    The real server answers a bad selector with isError and a message, not an
    HTTP error. Raising here would end the task on something the model could
    have recovered from.
    """
    transport = FakeTransport(
        HANDSHAKE,
        ok({"isError": True, "content": [{"type": "text", "text": "### Error\nNo such element"}]}),
    )

    result = await McpClient(transport).call("browser_click", {"target": "nope"})

    assert result.ok is False
    assert "No such element" in result.text


@pytest.mark.asyncio
async def test_a_protocol_error_is_reported_rather_than_raised() -> None:
    transport = FakeTransport(
        HANDSHAKE,
        {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "Invalid params"}},
    )

    result = await McpClient(transport).call("browser_click", {})

    assert result.ok is False
    assert "Invalid params" in result.text


@pytest.mark.asyncio
async def test_image_blocks_are_summarised_rather_than_inlined() -> None:
    """A screenshot is megabytes of base64 and would swamp the token budget."""
    transport = FakeTransport(
        HANDSHAKE,
        ok({"content": [
            {"type": "text", "text": "Took a screenshot"},
            {"type": "image", "data": "iVBORw0KGgo" * 5000, "mimeType": "image/png"},
        ]}),
    )

    result = await McpClient(transport).call("browser_take_screenshot", {})

    assert "iVBORw0KGgo" not in result.text
    assert "image" in result.text.lower()


@pytest.mark.asyncio
async def test_a_very_long_result_is_capped() -> None:
    """An accessibility snapshot of a large page runs to tens of thousands of
    characters, which is more than a per-minute token budget can carry."""
    transport = FakeTransport(HANDSHAKE, ok({"content": [{"type": "text", "text": "x" * 200_000}]}))

    result = await McpClient(transport).call("browser_snapshot", {})

    assert len(result.text) <= 6_000
    assert "truncated" in result.text.lower()


# --- when the server is not there -----------------------------------------


@pytest.mark.asyncio
async def test_an_unreachable_server_is_an_McpError_not_a_crash() -> None:
    transport = FakeTransport(ConnectionError("connection refused"))

    with pytest.raises(McpError):
        await McpClient(transport).list_tools()


@pytest.mark.asyncio
async def test_a_failed_tool_call_on_a_dead_server_is_reported() -> None:
    """Mid-task, the server going away must read as a failed step.

    The task can then end with an explanation rather than a stack trace.
    """
    transport = FakeTransport(HANDSHAKE, ConnectionError("server went away"))
    client = McpClient(transport)

    result = await client.call("browser_click", {"target": "e1"})

    assert result.ok is False
    assert "server went away" in result.text or "could not reach" in result.text.lower()


# --- the wire format ------------------------------------------------------


def test_it_reads_a_plain_json_response() -> None:
    assert parse_response('{"jsonrpc":"2.0","id":1,"result":{"a":1}}')["result"] == {"a": 1}


def test_it_reads_a_server_sent_events_response() -> None:
    """The real server answers in SSE framing even for a single reply."""
    raw = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"a":1}}\n\n'
    assert parse_response(raw)["result"] == {"a": 1}


def test_an_empty_body_is_not_an_error() -> None:
    """A notification is answered with 202 and nothing at all."""
    assert parse_response("") is None


def test_an_unparseable_body_raises() -> None:
    with pytest.raises(McpError):
        parse_response("<html>502 Bad Gateway</html>")


def test_a_tool_describes_itself_for_the_model() -> None:
    tool = McpTool(
        name="browser_click",
        description="Click an element",
        input_schema={"type": "object", "properties": {"target": {"type": "string"}}},
    )

    as_function = tool.to_openai_tool()
    assert as_function["type"] == "function"
    assert as_function["function"]["name"] == "browser_click"
    assert as_function["function"]["parameters"]["properties"]["target"]["type"] == "string"
