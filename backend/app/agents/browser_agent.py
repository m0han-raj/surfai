"""The agent loop that drives a browser through MCP tools.

Sits beside the existing orchestrator rather than replacing it, because the two
control different browsers and that difference matters more than the code they
share:

* the **orchestrator** acts on the user's actual tab, through the content
  script, and needs nothing installed;
* this loop acts on whatever browser the Playwright MCP server was pointed at,
  which is a separate browser unless that server was started with
  ``--cdp-endpoint`` or ``--extension``.

Conflating them would let "click the login button" act on a page the user is
not looking at, so they stay apart and the documentation says which is which.

Three rules carry over from the rest of SurfAI unchanged, because nothing
about a new tool transport makes them less true:

* a tool result is untrusted. It is page content that took a different route,
  so it is scanned for injection and wrapped in the same envelope;
* risk is classified in Python from the tool and its arguments, never by
  asking the model whether what it wants to do is dangerous;
* the loop is bounded. A model that has got stuck clicking the same thing
  should stop rather than spend a token budget discovering that.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.llm.prompts import TRUST_PREAMBLE
from app.llm.provider import LLMError, LLMProvider, Message
from app.mcp.client import McpClient, McpError, McpTool
from app.security.prompt_injection import scan as scan_for_injection
from app.security.sanitizer import wrap_untrusted

logger = logging.getLogger(__name__)

BROWSER_SYSTEM = f"""\
You are SurfAI's browser operator. You control a web browser through tools.

{TRUST_PREAMBLE}

Work in small steps. Look before you act: take a snapshot to see what is on the
page, then act on what you actually saw. Element references come from the
snapshot; never invent one, and never write a CSS selector when a reference
will do.

When a tool fails, read why and try a different approach rather than repeating
the same call. If you cannot do something, say so plainly.

Stop calling tools once you can answer, and then answer in plain language.
Describe what you did and what you found. Do not narrate tool names."""


#: Tool names that change the world rather than just look at it. Matched on
#: the name the server gave, so a server that adds new ones is not silently
#: assumed to be safe.
_WRITES = ("click", "type", "fill", "press_key", "select", "upload", "drag", "drop", "evaluate")

#: Words in an element description or URL that make an action worth stopping
#: for, whatever the tool. Deliberately the same categories the existing risk
#: classifier uses, because the danger is the act and not the transport.
_SENSITIVE = (
    "buy", "purchase", "order", "checkout", "pay", "payment", "subscribe",
    "delete", "remove", "close account", "deactivate",
    "send", "submit application", "post", "publish", "transfer",
    "password", "sign out", "log out",
)


@dataclass
class ToolStep:
    """One tool call, for the panel to show and the record to keep."""

    name: str
    arguments: dict[str, Any]
    ok: bool
    summary: str


@dataclass
class BrowserReply:
    message: str
    steps: list[ToolStep] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: True when the loop stopped because it ran out of calls, not answers.
    exhausted: bool = False


def needs_confirmation(name: str, arguments: dict[str, Any]) -> bool:
    """Should a human approve this call before it runs?

    Decided here, in Python, from the tool and its arguments. The model is
    never asked whether what it wants to do is dangerous, because a page that
    has talked it into an action can talk it into calling that action safe.
    """
    if not any(word in name.lower() for word in _WRITES):
        return False

    described = " ".join(
        str(value) for key, value in arguments.items() if isinstance(value, str)
    ).lower()
    return any(word in described for word in _SENSITIVE)


def _allowed(tools: list[McpTool]) -> list[McpTool]:
    """Narrow the discovered tools to the configured allowlist, if there is one.

    Discovery is still the server's job: this only decides how many of its
    tools are described to the model. It exists because those descriptions are
    the dominant token cost. Playwright MCP offers 24 tools, and their schemas
    come to roughly 5,000 tokens sent on every single turn -- measured against
    a free tier allowing 8,000 a minute, which left nothing for the page or
    the answer and rate-limited the task mid-run.
    """
    names = {name.strip() for name in settings.mcp_tool_filter.split(",") if name.strip()}
    if not names:
        return tools
    return [tool for tool in tools if tool.name in names]


def _summarise(result_text: str, limit: int = 160) -> str:
    """A line for the panel. The model gets the whole thing; the user gets this."""
    for line in result_text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped and not stripped.startswith("```"):
            return stripped[:limit]
    return "done"


class BrowserAgent:
    """Answers a request by driving a browser, one tool at a time."""

    def __init__(self, provider: LLMProvider, client: McpClient) -> None:
        self.provider = provider
        self.client = client

    async def run(
        self,
        request: str,
        *,
        max_calls: int | None = None,
        approve: Any = None,
    ) -> BrowserReply:
        """Work on `request` until the model answers or the budget runs out.

        `approve` is an optional callable taking (name, arguments) and
        returning whether to proceed; without one, actions that would need
        confirmation are refused rather than performed.
        """
        budget = max_calls or settings.mcp_max_tool_calls

        try:
            tools = await self.client.list_tools()
        except McpError as exc:
            return BrowserReply(
                message=(
                    "I could not reach the browser-control server, so I cannot drive the "
                    f"browser right now. ({exc})"
                )
            )

        if not tools:
            return BrowserReply(message="The browser-control server offered no tools.")

        tools = _allowed(tools)
        if not tools:
            return BrowserReply(
                message=(
                    "MCP_TOOL_FILTER excluded every tool this server offers, so there is "
                    "nothing I can do with the browser."
                )
            )

        messages = [
            Message(role="system", content=BROWSER_SYSTEM),
            Message(role="user", content=request),
        ]
        steps: list[ToolStep] = []
        warnings: list[str] = []
        schema = [tool.to_openai_tool() for tool in tools]

        for _ in range(budget):
            try:
                turn = await self.provider.tool_call(messages, tools=schema)
            except LLMError as exc:
                logger.info("Browser agent could not reach the model: %s", exc)
                return BrowserReply(
                    message=f"I lost contact with the language model partway through. ({exc})",
                    steps=steps,
                    warnings=warnings,
                )

            calls = turn.get("tool_calls") or []
            if not calls:
                # Nothing more to do: the model is answering.
                return BrowserReply(
                    message=turn.get("content") or "Done.", steps=steps, warnings=warnings
                )

            for call in calls:
                step, text, warning = await self._run_one(call, tools, approve)
                steps.append(step)
                if warning:
                    warnings.append(warning)
                messages.append(
                    Message(
                        role="user",
                        content=(
                            f"Result of {step.name}:\n"
                            # Untrusted, and labelled as such: a tool result is
                            # page content that arrived by another road.
                            + wrap_untrusted(text, "TOOL_RESULT")
                        ),
                    )
                )

        return BrowserReply(
            message=(
                "I stopped after "
                f"{len(steps)} browser actions without reaching an answer. "
                "Tell me more specifically what to look for and I will try again."
            ),
            steps=steps,
            warnings=warnings,
            exhausted=True,
        )

    async def _run_one(
        self, call: dict[str, Any], tools: list[McpTool], approve: Any
    ) -> tuple[ToolStep, str, str | None]:
        name = call.get("name", "")
        arguments = call.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}

        known = {tool.name for tool in tools}
        if name not in known:
            # A hallucinated tool name never reaches the server.
            text = f"There is no tool called {name}. Available tools: {', '.join(sorted(known))}"
            return ToolStep(name, arguments, False, "unknown tool"), text, None

        if needs_confirmation(name, arguments):
            approved = bool(approve and await approve(name, arguments))
            if not approved:
                text = (
                    "That action needs the user's confirmation and was not approved. "
                    "Do not retry it; continue with something else or explain what you need."
                )
                return ToolStep(name, arguments, False, "awaiting confirmation"), text, None

        result = await self.client.call(name, arguments)

        warning = None
        scan = scan_for_injection(result.text)
        if scan.is_suspicious:
            warning = (
                "A page reached through the browser tried to give the assistant "
                f"instructions ({', '.join(scan.categories)}). It was ignored."
            )
            logger.warning(
                "Prompt injection in an MCP tool result from %s: %s", name, scan.categories
            )

        text = scan.sanitized if scan.is_suspicious else result.text
        return ToolStep(name, arguments, result.ok, _summarise(text)), text, warning
