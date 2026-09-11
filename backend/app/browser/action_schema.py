"""Pydantic contract for browser actions.

Mirrors `shared/action-schema.ts`. Kept in sync by
`tests/test_action_validation.py::test_action_vocabulary_matches_shared_schema`,
which parses the TypeScript file so the two can never drift silently.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ActionType(StrEnum):
    CLICK = "CLICK"
    TYPE = "TYPE"
    SELECT = "SELECT"
    SCROLL = "SCROLL"
    NAVIGATE = "NAVIGATE"
    EXTRACT = "EXTRACT"
    WAIT = "WAIT"


class ScrollDirection(StrEnum):
    UP = "up"
    DOWN = "down"
    TOP = "top"
    BOTTOM = "bottom"


class BrowserAction(BaseModel):
    """A single action proposed by the planner.

    `extra="forbid"` matters for safety: a model cannot smuggle an extra field
    (`script`, `selector`, `js`) past validation hoping something downstream
    honours it.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    action: ActionType
    target: str | None = Field(default=None, max_length=64)
    value: str | None = Field(default=None, max_length=2000)
    direction: ScrollDirection | None = None
    timeout_ms: int | None = Field(default=None, ge=0, le=60_000)
    reason: str | None = Field(default=None, max_length=300)

    @field_validator("target")
    @classmethod
    def _target_is_semantic_id(cls, v: str | None) -> str | None:
        """Targets must be snapshot element ids (`e12`), never selectors.

        This is what stops the model from reaching arbitrary parts of the page
        or embedding script in a "selector".
        """
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if not (v.startswith("e") and v[1:].isdigit()):
            raise ValueError(
                f"target must be a semantic element id such as 'e3', received {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _check_required_fields(self) -> BrowserAction:
        action = self.action
        if action in (ActionType.CLICK, ActionType.TYPE, ActionType.SELECT) and not self.target:
            raise ValueError(f"{action} requires a target element id")
        if action in (ActionType.TYPE, ActionType.SELECT) and self.value is None:
            raise ValueError(f"{action} requires a value")
        if action == ActionType.NAVIGATE and not self.value:
            raise ValueError("NAVIGATE requires a URL in `value`")
        if action == ActionType.SCROLL and self.direction is None:
            self.direction = ScrollDirection.DOWN
        if action == ActionType.WAIT and self.timeout_ms is None:
            self.timeout_ms = 1000
        return self


class ActionResult(BaseModel):
    """Structured outcome reported back by the content script."""

    model_config = ConfigDict(extra="ignore")

    success: bool
    action: str
    target: str | None = None
    url_changed: bool = False
    page_changed: bool = False
    data: Any = None
    error: str | None = None
    duration_ms: int | None = None
    """Set by the executor; used to detect slow or hung pages."""


class SemanticElement(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: str
    tag: str = ""
    text: str | None = None
    placeholder: str | None = None
    ariaLabel: str | None = None  # noqa: N815 - matches the wire format
    name: str | None = None
    role: str | None = None
    inputType: str | None = None  # noqa: N815
    value: str | None = None
    href: str | None = None
    options: list[str] | None = None
    visible: bool = True
    disabled: bool = False
    level: int | None = None
    sensitive: bool = False


class SemanticPage(BaseModel):
    model_config = ConfigDict(extra="allow")

    url: str = ""
    domain: str = ""
    title: str = ""
    summary: str = ""
    elements: list[SemanticElement] = Field(default_factory=list)
    truncated: int = 0
    capturedAt: int | None = None  # noqa: N815

    def by_id(self) -> dict[str, SemanticElement]:
        return {e.id: e for e in self.elements}


class PlannerDecisionType(StrEnum):
    ACTION = "action"
    ANSWER = "answer"
    ASK = "ask"


class PlannerDecision(BaseModel):
    """The single structured object the planner is allowed to emit."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    type: PlannerDecisionType
    action: BrowserAction | None = None
    message: str | None = Field(default=None, max_length=4000)
    activity: str | None = Field(
        default=None,
        max_length=120,
        description="Short user-facing status line, e.g. 'Applying price filter'.",
    )

    @model_validator(mode="after")
    def _check_shape(self) -> PlannerDecision:
        if self.type == PlannerDecisionType.ACTION and self.action is None:
            raise ValueError("decision type 'action' requires an `action` object")
        if self.type in (PlannerDecisionType.ANSWER, PlannerDecisionType.ASK) and not self.message:
            raise ValueError(f"decision type '{self.type}' requires a `message`")
        return self


# JSON Schema handed to the model for structured output / grammar constraint.
PLANNER_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["type"],
    "properties": {
        "type": {"type": "string", "enum": ["action", "answer", "ask"]},
        "activity": {"type": "string"},
        "message": {"type": "string"},
        "action": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action"],
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [a.value for a in ActionType],
                },
                "target": {"type": "string"},
                "value": {"type": "string"},
                "direction": {"type": "string", "enum": [d.value for d in ScrollDirection]},
                "timeout_ms": {"type": "integer"},
                "reason": {"type": "string"},
            },
        },
    },
}


class ConfirmationDecision(BaseModel):
    """User's answer to a confirmation prompt."""

    model_config = ConfigDict(extra="forbid")

    task_id: str
    approved: bool
    remember: Literal["once", "session"] = "once"
