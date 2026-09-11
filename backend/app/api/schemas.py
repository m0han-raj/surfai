"""Request and response models for the HTTP API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TabContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str = ""
    title: str = ""
    tab_id: int | None = None


class ChatTurn(BaseModel):
    """One prior turn, replayed so a direct answer has conversational context."""

    model_config = ConfigDict(extra="ignore")

    role: Literal["user", "assistant"]
    content: str = Field(default="", max_length=4000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str = Field(min_length=1, max_length=4000)
    page_context: dict[str, Any] = Field(default_factory=dict)
    tab_context: TabContext = Field(default_factory=TabContext)
    task_id: str | None = None
    # The thread to append to. Absent starts a new one; unknown, or somebody
    # else's, also starts a new one rather than failing.
    conversation_id: str | None = None
    # Recent turns only. The backend holds no conversation state; the panel
    # sends what it wants remembered, and the assistant caps it.
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


class ContinueRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    page_context: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    confirmation: bool | None = None


class DirectiveResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    task_id: str
    state: str
    activity: str = ""
    step: int = 0
    max_steps: int = 0
    action: dict[str, Any] | None = None
    risk: dict[str, Any] | None = None
    message: str = ""
    data: Any = None
    warnings: list[str] = Field(default_factory=list)
    favourite: dict[str, Any] | None = None
    favourites: list[dict[str, Any]] | None = None
    intent: str | None = None
    # Where this turn was recorded. The panel sends it back on the next turn
    # so the exchange joins the same thread.
    conversation_id: str | None = None


class FavouriteCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    domain: str | None = Field(default=None, max_length=255)
    intent: str = Field(default="", max_length=2000)
    description: str = Field(default="", max_length=2000)
    preferences: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FavouriteUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=2000)
    domain: str | None = Field(default=None, max_length=255)
    intent: str | None = Field(default=None, max_length=2000)
    description: str | None = Field(default=None, max_length=2000)
    preferences: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class FavouriteResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    url: str
    domain: str
    intent: str
    description: str
    preferences: dict[str, Any]
    metadata: dict[str, Any]
    created_at: str | None = None
    updated_at: str | None = None


class TaskCreate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    request: str = Field(min_length=1, max_length=4000)
    page_context: dict[str, Any] = Field(default_factory=dict)
    tab_context: TabContext = Field(default_factory=TabContext)
    favourite_id: str | None = None


class ObserveRequest(BaseModel):
    """Analyse a page without starting a task -- used by the Current Page panel."""

    model_config = ConfigDict(extra="ignore")

    page_context: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    detail: str
    code: str = "error"
