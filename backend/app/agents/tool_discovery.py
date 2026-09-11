"""Tool Discovery: infer what a website can do.

A "tool" here is a *description* bound to element ids -- `search_products(query)`
mapped to `[TYPE e3, CLICK e4]`. It is never executable code produced by a
model. Expansion into concrete actions happens in `expand_tool`, in Python,
and the result still passes through the normal validator.

Heuristic discovery runs first and covers the common shapes (search box +
button, dropdown filters, pagination). The LLM pass is optional enrichment for
pages the heuristics do not recognise.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from app.agents.page_agent import PageUnderstanding
from app.browser.action_schema import SemanticPage
from app.llm.prompts import TOOL_DISCOVERY_SYSTEM, format_page_context
from app.llm.provider import LLMError, LLMProvider, Message

logger = logging.getLogger(__name__)


@dataclass
class ToolParameter:
    name: str
    type: str = "string"
    required: bool = True
    description: str = ""
    options: list[str] = field(default_factory=list)


@dataclass
class DiscoveredTool:
    name: str
    description: str
    element_ids: list[str] = field(default_factory=list)
    parameters: list[ToolParameter] = field(default_factory=list)
    action_template: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


TOOL_DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["tools"],
    "additionalProperties": False,
    "properties": {
        "tools": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "description", "element_ids"],
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "element_ids": {"type": "array", "items": {"type": "string"}},
                    "parameters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["name"],
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string"},
                                "type": {"type": "string"},
                                "required": {"type": "boolean"},
                                "description": {"type": "string"},
                            },
                        },
                    },
                },
            },
        }
    },
}

_PRICE_HINT = re.compile(r"price|cost|budget|amount|₹|\$|max|min", re.IGNORECASE)
_LOCATION_HINT = re.compile(r"location|city|region|place|where", re.IGNORECASE)
_NEXT_HINT = re.compile(r"next|more|older|forward", re.IGNORECASE)
_SNAKE = re.compile(r"[^a-z0-9_]+")


class ToolDiscovery:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider

    # -- heuristics -------------------------------------------------------

    def discover_heuristic(
        self, understanding: PageUnderstanding, page: SemanticPage
    ) -> list[DiscoveredTool]:
        elements = page.by_id()
        tools: list[DiscoveredTool] = []

        # search(query)
        if understanding.search_input_ids:
            input_id = understanding.search_input_ids[0]
            template: list[dict[str, Any]] = [
                {"action": "TYPE", "target": input_id, "value": "{query}"}
            ]
            ids = [input_id]
            if understanding.search_button_ids:
                button_id = understanding.search_button_ids[0]
                template.append({"action": "CLICK", "target": button_id})
                ids.append(button_id)
            tools.append(
                DiscoveredTool(
                    name="search",
                    description=(
                        "Search this site using the "
                        f"'{_describe(elements.get(input_id))}' field"
                    ),
                    element_ids=ids,
                    parameters=[
                        ToolParameter("query", "string", True, "Text to search for")
                    ],
                    action_template=template,
                    confidence=0.9 if understanding.search_button_ids else 0.7,
                )
            )

        # filter_*(value) -- one tool per distinct filter control
        for element_id in understanding.filter_ids[:10]:
            element = elements.get(element_id)
            if element is None:
                continue
            label = _describe(element)
            slug = _slugify(label) or element_id
            if element.type == "select":
                tools.append(
                    DiscoveredTool(
                        name=f"filter_{slug}",
                        description=f"Filter results by {label}",
                        element_ids=[element_id],
                        parameters=[
                            ToolParameter(
                                "value",
                                "enum",
                                True,
                                f"One of the available {label} options",
                                options=list(element.options or []),
                            )
                        ],
                        action_template=[
                            {"action": "SELECT", "target": element_id, "value": "{value}"}
                        ],
                        confidence=0.85,
                    )
                )
            elif element.type in ("checkbox", "radio", "button"):
                tools.append(
                    DiscoveredTool(
                        name=f"toggle_{slug}",
                        description=f"Toggle the '{label}' filter",
                        element_ids=[element_id],
                        parameters=[],
                        action_template=[{"action": "CLICK", "target": element_id}],
                        confidence=0.75,
                    )
                )
            elif element.type in ("input", "textarea"):
                kind = "price" if _PRICE_HINT.search(label) else (
                    "location" if _LOCATION_HINT.search(label) else "value"
                )
                tools.append(
                    DiscoveredTool(
                        name=f"set_{slug}",
                        description=f"Set the {label} field",
                        element_ids=[element_id],
                        parameters=[ToolParameter(kind, "string", True, f"Value for {label}")],
                        action_template=[
                            {"action": "TYPE", "target": element_id, "value": f"{{{kind}}}"}
                        ],
                        confidence=0.75,
                    )
                )

        # next_page()
        for element_id in understanding.pagination_ids:
            element = elements.get(element_id)
            if element is None:
                continue
            if _NEXT_HINT.search(_describe(element)):
                tools.append(
                    DiscoveredTool(
                        name="next_page",
                        description="Go to the next page of results",
                        element_ids=[element_id],
                        parameters=[],
                        action_template=[{"action": "CLICK", "target": element_id}],
                        confidence=0.85,
                    )
                )
                break

        # extract_results()
        if understanding.has_results:
            tools.append(
                DiscoveredTool(
                    name="extract_results",
                    description="Read the visible results from the page",
                    element_ids=understanding.result_link_ids[:20],
                    parameters=[],
                    action_template=[{"action": "EXTRACT"}],
                    confidence=0.8,
                )
            )

        # open_result(index)
        if understanding.result_link_ids:
            tools.append(
                DiscoveredTool(
                    name="open_result",
                    description="Open one of the results by its element id",
                    element_ids=understanding.result_link_ids[:20],
                    parameters=[
                        ToolParameter("element_id", "string", True, "Element id of the result")
                    ],
                    action_template=[{"action": "CLICK", "target": "{element_id}"}],
                    confidence=0.8,
                )
            )

        return _dedupe(tools)

    # -- LLM enrichment ---------------------------------------------------

    async def discover_llm(
        self, understanding: PageUnderstanding, page: SemanticPage
    ) -> list[DiscoveredTool]:
        if self.provider is None:
            return []
        messages = [
            Message(role="system", content=TOOL_DISCOVERY_SYSTEM),
            Message(
                role="user",
                content=(
                    f"Page type: {understanding.page_type}\n\n"
                    f"{format_page_context(page.model_dump())}\n\n"
                    "List the capabilities this page offers, as JSON matching the schema."
                ),
            ),
        ]
        try:
            parsed = await self.provider.generate_structured(
                messages, schema=TOOL_DISCOVERY_SCHEMA, schema_name="tool_discovery"
            )
        except LLMError as exc:
            logger.info("LLM tool discovery unavailable: %s", exc)
            return []

        valid_ids = set(page.by_id())
        tools: list[DiscoveredTool] = []
        for raw in parsed.get("tools", [])[:12]:
            # Drop any tool referencing an element that is not really there.
            ids = [i for i in raw.get("element_ids", []) if i in valid_ids]
            if not ids:
                continue
            tools.append(
                DiscoveredTool(
                    name=_slugify(raw.get("name", "")) or "tool",
                    description=str(raw.get("description", ""))[:200],
                    element_ids=ids,
                    parameters=[
                        ToolParameter(
                            name=str(p.get("name", "value")),
                            type=str(p.get("type", "string")),
                            required=bool(p.get("required", True)),
                            description=str(p.get("description", ""))[:120],
                        )
                        for p in raw.get("parameters", [])[:5]
                    ],
                    action_template=[],
                    confidence=0.6,
                )
            )
        return tools

    async def discover(
        self,
        understanding: PageUnderstanding,
        page: SemanticPage,
        *,
        use_llm: bool = False,
    ) -> dict[str, Any]:
        heuristic = self.discover_heuristic(understanding, page)
        tools = heuristic
        source = "heuristic"

        # Only pay for a model call when the heuristics found little.
        if use_llm and self.provider is not None and len(heuristic) < 2:
            llm_tools = await self.discover_llm(understanding, page)
            if llm_tools:
                tools = _dedupe([*heuristic, *llm_tools])
                source = "merged" if heuristic else "llm"

        return {
            "url": page.url,
            "domain": understanding.domain,
            "source": source,
            "tools": [t.to_dict() for t in tools],
        }


def expand_tool(tool: DiscoveredTool, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    """Expand a tool into concrete actions by substituting `{param}` slots.

    Substitution is textual and applies only to the `value` and `target`
    fields of a fixed template written by *us*, never by the model.
    """
    actions: list[dict[str, Any]] = []
    for step in tool.action_template:
        concrete = dict(step)
        for key in ("value", "target"):
            raw = concrete.get(key)
            if isinstance(raw, str) and raw.startswith("{") and raw.endswith("}"):
                param = raw[1:-1]
                if param not in arguments:
                    raise ValueError(f"Tool '{tool.name}' requires argument '{param}'")
                concrete[key] = str(arguments[param])
        actions.append(concrete)
    return actions


def _describe(element: Any) -> str:
    if element is None:
        return "field"
    for key in ("ariaLabel", "placeholder", "text", "name"):
        value = getattr(element, key, None) if not isinstance(element, dict) else element.get(key)
        if value:
            return str(value)[:60]
    return "field"


def _slugify(text: str) -> str:
    return _SNAKE.sub("_", str(text).lower().strip()).strip("_")[:40]


def _dedupe(tools: list[DiscoveredTool]) -> list[DiscoveredTool]:
    seen: dict[str, DiscoveredTool] = {}
    for tool in tools:
        existing = seen.get(tool.name)
        if existing is None or tool.confidence > existing.confidence:
            seen[tool.name] = tool
    return list(seen.values())
