"""A Model Context Protocol client, over Streamable HTTP.

Hand-rolled rather than built on the official `mcp` SDK, for a measured
reason. The SDK and its dependencies add 28MB to a deployment whose ceiling is
50MB and which already sits at 47MB:

    current deployment deps : 47 MB
    with the MCP SDK        : 75 MB

The slice of the protocol SurfAI needs is small -- initialize, the initialized
notification, tools/list, tools/call -- and every shape here was checked
against a real `@playwright/mcp` server before it was written, including the
detail that it answers in SSE framing even for a single reply.

Two things learned from that server shape the design. Tool schemas must be
discovered, never assumed: `browser_find` takes `text` rather than `query` and
`browser_click` takes `target` rather than `ref`, both of which are easy to
guess wrong. And a session is a browser context: drop the session header and
the next call runs against a fresh `about:blank`, silently undoing everything
before it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: What SurfAI claims to support. The server echoes its own version back.
PROTOCOL_VERSION = "2025-06-18"

#: Ceiling on a single tool result, unless the caller says otherwise.
#:
#: An accessibility snapshot of a real site runs past 20,000 characters, which
#: is roughly 5,000 tokens. Against a free tier's 8,000 per minute that is most
#: of the budget in one call, and two of them rate-limited a task mid-run while
#: this was being built. The default is deliberately well under that.
MAX_RESULT_CHARS = 6_000


class McpError(RuntimeError):
    """The server could not be reached, or did not speak MCP."""


@dataclass
class McpTool:
    """A tool the server offers, exactly as the server described it."""

    name: str
    description: str
    input_schema: dict[str, Any]

    def to_openai_tool(self) -> dict[str, Any]:
        """The same tool in the shape chat-completions tool calling expects."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema or {"type": "object", "properties": {}},
            },
        }


@dataclass
class McpResult:
    """The outcome of one tool call, as something the agent can read."""

    ok: bool
    text: str


class Transport(Protocol):
    """One HTTP round trip. Returns the body and any session id offered."""

    async def post(self, body: dict, headers: dict) -> tuple[str, str | None]: ...


def parse_response(raw: str) -> dict | None:
    """Read a JSON-RPC reply, whether framed as JSON or as SSE.

    The real server answers with `event: message` / `data: {...}` even when
    there is exactly one reply, so handling only `application/json` works
    against a specification and not against the thing it describes.
    """
    if not raw.strip():
        # A notification is acknowledged with 202 and an empty body.
        return None

    for line in raw.splitlines():
        if line.startswith("data:"):
            try:
                return json.loads(line[5:].strip())
            except json.JSONDecodeError as exc:
                raise McpError(f"MCP server sent an unreadable event: {exc}") from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise McpError(f"MCP server sent a non-JSON response: {raw[:200]}") from exc


def _render(content: list[dict[str, Any]], limit: int = MAX_RESULT_CHARS) -> str:
    """Flatten a tool result's content blocks into text for the model.

    Images are named rather than carried. A screenshot is megabytes of base64,
    and inlining one would spend an entire token budget describing a picture
    the model may not even need.
    """
    parts: list[str] = []
    for block in content or []:
        kind = block.get("type")
        if kind == "text":
            parts.append(str(block.get("text", "")))
        elif kind == "image":
            parts.append(f"[an image was returned ({block.get('mimeType', 'image')})]")
        elif kind == "resource":
            parts.append(f"[a resource was returned: {block.get('uri', 'unknown')}]")

    text = "\n".join(part for part in parts if part)
    if len(text) > limit:
        dropped = len(text) - limit
        notice = f"\n... (truncated, {dropped} more characters)"
        # The cap covers the notice too: the whole string is what costs
        # tokens, so announcing a limit must not be the thing that exceeds it.
        text = text[: limit - len(notice)] + notice
    return text


class McpClient:
    """Talks to one MCP server, holding one session for its lifetime."""

    def __init__(self, transport: Transport, max_result_chars: int = MAX_RESULT_CHARS) -> None:
        self._transport = transport
        self._max_result_chars = max_result_chars
        self._session_id: str | None = None
        self._initialised = False
        self._next_id = 0

    # -- protocol ---------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            # Both, because the server chooses which to answer with.
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def _send(self, method: str, params: dict | None = None, *, notify: bool = False):
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not notify:
            self._next_id += 1
            body["id"] = self._next_id

        try:
            raw, session_id = await self._transport.post(body, self._headers())
        except McpError:
            raise
        except Exception as exc:  # noqa: BLE001 - every transport failure looks alike here
            raise McpError(f"Could not reach the MCP server: {exc}") from exc

        # The session is handed out on initialize and must ride along after:
        # one session is one browser context, and losing it silently resets
        # the browser between steps of the same task.
        if session_id:
            self._session_id = session_id

        message = parse_response(raw)
        if message is None:
            return None
        if "error" in message:
            error = message["error"]
            raise McpError(f"{error.get('message', 'MCP error')} (code {error.get('code')})")
        return message.get("result")

    async def _ensure_initialised(self) -> None:
        if self._initialised:
            return
        await self._send(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "surfai", "version": "0.1.0"},
            },
        )
        await self._send("notifications/initialized", {}, notify=True)
        self._initialised = True

    # -- what callers use -------------------------------------------------

    async def list_tools(self) -> list[McpTool]:
        """Every tool the server offers, as it describes them.

        Never a hard-coded list: the names and argument shapes belong to the
        server's version, not to whatever was true when this was written.
        """
        await self._ensure_initialised()
        result = await self._send("tools/list") or {}
        return [
            McpTool(
                name=tool.get("name", ""),
                description=tool.get("description", ""),
                input_schema=tool.get("inputSchema") or {},
            )
            for tool in result.get("tools", [])
            if tool.get("name")
        ]

    async def call(self, name: str, arguments: dict[str, Any]) -> McpResult:
        """Run one tool. Failure is reported, never raised.

        A bad selector, a closed page or a navigation timeout are all things
        the agent can read and recover from by trying something else; raising
        would end a task on a step the model could have worked around.
        """
        try:
            await self._ensure_initialised()
            result = await self._send("tools/call", {"name": name, "arguments": arguments}) or {}
        except McpError as exc:
            logger.info("MCP tool %s failed: %s", name, exc)
            return McpResult(ok=False, text=str(exc))

        return McpResult(
            ok=not result.get("isError", False),
            text=_render(result.get("content"), self._max_result_chars),
        )
