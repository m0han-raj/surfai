"""HTTP transport for the MCP client, and the wiring that builds one.

Separate from the client so the protocol can be tested without a socket, and
so the client does not care whether it is talking to Playwright MCP or to
anything else that speaks the same thing.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.mcp.client import McpClient

logger = logging.getLogger(__name__)


class HttpTransport:
    """One MCP endpoint, over Streamable HTTP."""

    def __init__(self, url: str, timeout: float = 60.0) -> None:
        self.url = url
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0))

    async def post(self, body: dict, headers: dict) -> tuple[str, str | None]:
        response = await self._client.post(self.url, json=body, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(
                f"MCP server returned HTTP {response.status_code}: {response.text[:200]}"
            )
        # The session id arrives on the initialize response and must be carried
        # afterwards; one session is one browser context.
        return response.text, response.headers.get("mcp-session-id")

    async def aclose(self) -> None:
        await self._client.aclose()


def build_client() -> McpClient | None:
    """An MCP client for the configured server, or None if it is switched off.

    Browser control is optional and off by default. It needs a Playwright MCP
    server running beside the browser, which the hosted deployment can never
    have: a server on Vercel cannot reach a Chrome on somebody's laptop. So
    absence is the normal case, not an error, and everything SurfAI could do
    before it keeps working without it.
    """
    if not settings.mcp_enabled:
        return None
    if not settings.mcp_server_url:
        logger.warning("MCP_ENABLED is set but MCP_SERVER_URL is empty; browser control is off")
        return None

    return McpClient(
        HttpTransport(settings.mcp_server_url, settings.mcp_timeout_s),
        max_result_chars=settings.mcp_max_result_chars,
    )
