"""Orchestrator: owns the agent loop and the task lifecycle.

Execution model
---------------
Actions happen in the browser, not on the server, so the loop is *server-driven,
client-executed*. The backend decides one step, hands the extension a directive,
and the extension executes it and calls back with the outcome **and a fresh page
snapshot**. That round trip is the "observe" edge of the loop and is what makes
re-planning real rather than decorative:

    start(message, page)  -> directive
    continue(result, page) -> directive
    ...
    directive.type == "answer" | "error"

Session state lives in memory (single local user, MVP). Tasks, actions and
results are persisted so history survives a restart; an in-flight task whose
session is lost is reported as failed rather than silently resumed.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from app.agents.memory_agent import MemoryAgent
from app.agents.page_agent import PageAgent
from app.agents.planner import Planner, PlannerContext, PlannerError
from app.agents.tool_discovery import ToolDiscovery
from app.browser.action_schema import BrowserAction
from app.browser.risk import classify as classify_risk
from app.config import settings
from app.llm.prompts import format_extracted
from app.llm.provider import LLMProvider, LLMUnavailableError

logger = logging.getLogger(__name__)

# Agent states (mirrors shared/types.ts).
IDLE = "IDLE"
ANALYZING = "ANALYZING"
OBSERVING = "OBSERVING"
PLANNING = "PLANNING"
WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
EXECUTING = "EXECUTING"
VERIFYING = "VERIFYING"
REPLANNING = "REPLANNING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"

TERMINAL_STATES = {COMPLETED, FAILED, CANCELLED}

# How many stopped task ids to remember so a late callback can be answered
# accurately. Bounded so a long-running process cannot grow without limit.
MAX_REMEMBERED_CANCELLATIONS = 64


@dataclass
class Directive:
    """What the backend asks the extension to do next."""

    type: str  # action | confirm | answer | ask | error
    task_id: str
    state: str
    activity: str = ""
    step: int = 0
    max_steps: int = settings.max_agent_steps
    action: dict[str, Any] | None = None
    risk: dict[str, Any] | None = None
    message: str = ""
    data: Any = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "task_id": self.task_id,
            "state": self.state,
            "activity": self.activity,
            "step": self.step,
            "max_steps": self.max_steps,
            "action": self.action,
            "risk": self.risk,
            "message": self.message,
            "data": self.data,
            "warnings": self.warnings,
        }


@dataclass
class Session:
    """State for one running task.

    Serialisable, because the agent loop spans many HTTP round trips and the
    state has to survive both a restart and a request landing on a different
    instance. `to_dict`/`from_dict` are the whole contract with the store.
    """

    task_id: str
    goal: str
    user_message: str
    user_id: str = ""
    state: str = IDLE
    step: int = 0
    consecutive_failures: int = 0
    # Wall clock rather than a monotonic counter: elapsed time has to mean
    # something across processes.
    started_at: float = field(default_factory=time.time)
    history: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    favourite: dict[str, Any] | None = None
    extracted: list[str] = field(default_factory=list)
    pending_action: BrowserAction | None = None
    pending_risk: dict[str, Any] | None = None
    cancelled: bool = False
    current_url: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def elapsed(self) -> float:
        return max(0.0, time.time() - self.started_at)

    def extracted_text(self) -> str | None:
        if not self.extracted:
            return None
        joined = "\n\n".join(self.extracted[-3:])
        return format_extracted(joined)

    # -- serialisation ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "goal": self.goal,
            "user_message": self.user_message,
            "user_id": self.user_id,
            "state": self.state,
            "step": self.step,
            "consecutive_failures": self.consecutive_failures,
            "started_at": self.started_at,
            "history": self.history,
            "tools": self.tools,
            "favourite": self.favourite,
            "extracted": self.extracted,
            "pending_action": (
                self.pending_action.model_dump() if self.pending_action else None
            ),
            "pending_risk": self.pending_risk,
            "cancelled": self.cancelled,
            "current_url": self.current_url,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        pending = data.get("pending_action")
        action: BrowserAction | None = None
        if pending:
            try:
                action = BrowserAction.model_validate(pending)
            except Exception:  # noqa: BLE001
                # A stored action that no longer validates against the current
                # schema is dropped rather than trusted. The task then asks the
                # planner again, which is the safe direction.
                logger.warning("Discarded an unparseable pending action on restore")

        return cls(
            task_id=str(data.get("task_id", "")),
            goal=str(data.get("goal", "")),
            user_message=str(data.get("user_message", "")),
            user_id=str(data.get("user_id", "")),
            state=str(data.get("state", IDLE)),
            step=int(data.get("step", 0)),
            consecutive_failures=int(data.get("consecutive_failures", 0)),
            started_at=float(data.get("started_at", time.time())),
            history=list(data.get("history") or []),
            tools=list(data.get("tools") or []),
            favourite=data.get("favourite"),
            extracted=list(data.get("extracted") or []),
            pending_action=action,
            pending_risk=data.get("pending_risk"),
            cancelled=bool(data.get("cancelled", False)),
            current_url=str(data.get("current_url", "")),
            warnings=list(data.get("warnings") or []),
        )


class SessionStore:
    """Where in-flight agent state lives.

    Narrow on purpose: the orchestrator does not care whether this is a dict or
    a database, which keeps the loop testable without either.
    """

    def load(self, task_id: str, user_id: str) -> Session | None:
        ...

    def save(self, session: Session) -> None:
        ...

    def delete(self, task_id: str) -> None:
        ...


class MemorySessionStore(SessionStore):
    """Single-process store. Correct for local use and for tests."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def load(self, task_id: str, user_id: str) -> Session | None:
        session = self._sessions.get(task_id)
        # A task belongs to the user who started it, whatever the store.
        if session is None or (session.user_id and user_id and session.user_id != user_id):
            return None
        return session

    def save(self, session: Session) -> None:
        self._sessions[session.task_id] = session

    def delete(self, task_id: str) -> None:
        self._sessions.pop(task_id, None)


class TaskStore:
    """Persistence hook.

    The orchestrator is written against this narrow interface so it can be
    exercised in tests with no database at all.
    """

    def create_task(self, task_id: str, request: str, url: str, favourite_id: str | None) -> None:
        ...

    def update_task(self, task_id: str, **fields: Any) -> None:
        ...

    def record_action(
        self, task_id: str, step: int, action: dict[str, Any], result: dict[str, Any], status: str
    ) -> None:
        ...


class NullTaskStore(TaskStore):
    def create_task(self, *args: Any, **kwargs: Any) -> None:
        return None

    def update_task(self, *args: Any, **kwargs: Any) -> None:
        return None

    def record_action(self, *args: Any, **kwargs: Any) -> None:
        return None


class Orchestrator:
    def __init__(
        self,
        provider: LLMProvider,
        store: TaskStore | None = None,
        session_store: SessionStore | None = None,
        *,
        max_steps: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        self.provider = provider
        self.store = store or NullTaskStore()
        self.max_steps = max_steps or settings.max_agent_steps
        self.max_retries = max_retries if max_retries is not None else settings.max_retries
        self.planner = Planner(provider)
        self.page_agent = PageAgent(provider)
        self.tool_discovery = ToolDiscovery(provider)
        self.memory = MemoryAgent(provider)
        self.sessions = session_store or MemorySessionStore()
        # Ids of tasks the user stopped. A cancelled session is dropped
        # immediately, but an in-flight client may still report the result of
        # the action it was running; without this it would be told the task
        # "is no longer active", which reads as a crash rather than as the
        # cancellation the user just asked for.
        self._recently_cancelled: OrderedDict[str, None] = OrderedDict()

    # -- session access ---------------------------------------------------

    def get_session(self, task_id: str, user_id: str = "") -> Session | None:
        return self.sessions.load(task_id, user_id)

    def drop_session(self, task_id: str) -> None:
        self.sessions.delete(task_id)

    def _remember_cancelled(self, task_id: str) -> None:
        self._recently_cancelled[task_id] = None
        self._recently_cancelled.move_to_end(task_id)
        while len(self._recently_cancelled) > MAX_REMEMBERED_CANCELLATIONS:
            self._recently_cancelled.popitem(last=False)

    # -- lifecycle --------------------------------------------------------

    async def start(
        self,
        *,
        task_id: str,
        message: str,
        page: dict[str, Any],
        favourite: dict[str, Any] | None = None,
        user_id: str = "",
    ) -> Directive:
        """Begin a browsing task and return the first directive."""
        goal = message
        if favourite:
            goal = MemoryAgent.build_goal(favourite, message)

        session = Session(
            task_id=task_id,
            goal=goal,
            user_message=message,
            user_id=user_id,
            favourite=favourite,
            current_url=str(page.get("url", "")),
        )
        session.state = ANALYZING
        self.sessions.save(session)

        self.store.create_task(
            task_id, message, session.current_url, (favourite or {}).get("id")
        )
        self.store.update_task(task_id, status=ANALYZING)

        return await self._advance(session, page)

    async def continue_task(
        self,
        *,
        task_id: str,
        page: dict[str, Any],
        result: dict[str, Any] | None = None,
        confirmation: bool | None = None,
        user_id: str = "",
    ) -> Directive:
        """Report an action's outcome and get the next directive."""
        session = self.sessions.load(task_id, user_id)
        if session is None:
            if task_id in self._recently_cancelled:
                # The client was mid-action when the user pressed Stop.
                return Directive(
                    type="error",
                    task_id=task_id,
                    state=CANCELLED,
                    activity="Task cancelled",
                    message="Task cancelled.",
                )
            return Directive(
                type="error",
                task_id=task_id,
                state=FAILED,
                message=(
                    "This task is no longer active. The backend may have restarted. "
                    "Please start it again."
                ),
            )

        if session.cancelled:
            return self._terminal(session, CANCELLED, "Task cancelled.")

        # -- confirmation gate --------------------------------------------
        if session.state == WAITING_CONFIRMATION:
            if confirmation is None:
                return self._confirm_directive(session)
            if not confirmation:
                pending = session.pending_action
                session.pending_action = None
                session.pending_risk = None
                session.history.append(
                    {
                        "step": session.step,
                        "action": pending.model_dump() if pending else {},
                        "result": {"success": False, "error": "Declined by the user"},
                    }
                )
                self.store.record_action(
                    task_id,
                    session.step,
                    pending.model_dump() if pending else {},
                    {"success": False, "error": "declined"},
                    "DECLINED",
                )
                return self._terminal(
                    session,
                    CANCELLED,
                    "Action declined, so the task was stopped. Tell me what to do instead.",
                )

            # Approved: hand the pending action to the extension.
            action = session.pending_action
            session.pending_action = None
            session.state = EXECUTING
            self.store.update_task(task_id, status=EXECUTING)
            self.sessions.save(session)
            return Directive(
                type="action",
                task_id=task_id,
                state=EXECUTING,
                activity="Executing confirmed action",
                step=session.step,
                max_steps=self.max_steps,
                action=action.model_dump() if action else None,
                risk=session.pending_risk,
            )

        # -- record the outcome of the previous action --------------------
        if result is not None:
            self._record_result(session, result)

            if not result.get("success", False):
                session.consecutive_failures += 1
                if session.consecutive_failures > self.max_retries:
                    return self._terminal(
                        session,
                        FAILED,
                        _failure_message(result, session.consecutive_failures),
                    )
                session.state = REPLANNING
            else:
                session.consecutive_failures = 0
                session.state = VERIFYING

        return await self._advance(session, page)

    def cancel(self, task_id: str, user_id: str = "") -> Directive:
        """Stop a task. Idempotent, and never loses history."""
        self._remember_cancelled(task_id)
        session = self.sessions.load(task_id, user_id)
        if session is None:
            self.store.update_task(task_id, status=CANCELLED, completed=True)
            return Directive(
                type="answer",
                task_id=task_id,
                state=CANCELLED,
                activity="Task cancelled",
                message="Task cancelled.",
            )
        session.cancelled = True
        session.pending_action = None
        return self._terminal(session, CANCELLED, "Task cancelled.")

    # -- the loop ---------------------------------------------------------

    async def _advance(self, session: Session, raw_page: dict[str, Any]) -> Directive:
        """One turn of observe -> understand -> discover -> plan -> validate."""
        if session.cancelled:
            return self._terminal(session, CANCELLED, "Task cancelled.")

        if session.step >= self.max_steps:
            return self._terminal(
                session,
                FAILED,
                f"Stopped after {self.max_steps} steps without completing the task. "
                "Try a narrower request, or tell me the next step directly.",
            )

        if session.elapsed > settings.task_timeout_s:
            return self._terminal(
                session,
                FAILED,
                f"Task timed out after {int(session.elapsed)} seconds.",
            )

        # --- OBSERVE ------------------------------------------------------
        session.state = OBSERVING
        self.store.update_task(session.task_id, status=OBSERVING)
        understanding, page, scan = self.page_agent.analyze(raw_page or {})
        session.last_understanding = understanding
        session.current_url = page.url or session.current_url

        injection_warning = None
        if scan.is_suspicious:
            injection_warning = ", ".join(scan.categories)
            warning = (
                f"This page contains text that tried to give the assistant instructions "
                f"({injection_warning}). It was ignored."
            )
            if warning not in session.warnings:
                session.warnings.append(warning)
            logger.warning(
                "Prompt injection detected on %s: %s", page.url, scan.categories
            )

        # --- DISCOVER -----------------------------------------------------
        discovery = await self.tool_discovery.discover(understanding, page, use_llm=False)
        session.tools = discovery["tools"]

        # --- PLAN ---------------------------------------------------------
        session.state = PLANNING
        self.store.update_task(session.task_id, status=PLANNING, current_url=page.url)

        context = PlannerContext(
            goal=session.goal,
            page=page,
            history=session.history,
            tools=session.tools,
            favourite=session.favourite,
            extracted=session.extracted_text(),
            step=session.step,
            max_steps=self.max_steps,
            injection_warning=injection_warning,
        )

        try:
            plan = await self.planner.next_step(context)
        except LLMUnavailableError as exc:
            return self._terminal(
                session,
                FAILED,
                "I could not reach the language model. Check that your LLM runtime is "
                f"running and that LLM_BASE_URL is correct. ({exc})",
            )
        except PlannerError as exc:
            session.consecutive_failures += 1
            if session.consecutive_failures > self.max_retries:
                return self._terminal(
                    session, FAILED, f"I could not work out a valid next step. ({exc})"
                )
            # Re-observe and try again on the next round trip.
            session.state = REPLANNING
            self.sessions.save(session)
            return Directive(
                type="action",
                task_id=session.task_id,
                state=REPLANNING,
                activity="Re-planning",
                step=session.step,
                max_steps=self.max_steps,
                action={"action": "WAIT", "timeout_ms": 500, "reason": "re-observing the page"},
                warnings=session.warnings,
            )

        if plan.decision.type == "answer":
            return self._terminal(session, COMPLETED, plan.message)

        if plan.decision.type == "ask":
            session.state = WAITING_CONFIRMATION
            self.store.update_task(session.task_id, status=WAITING_CONFIRMATION)
            self.sessions.save(session)
            return Directive(
                type="ask",
                task_id=session.task_id,
                state=WAITING_CONFIRMATION,
                activity="Awaiting your input",
                step=session.step,
                max_steps=self.max_steps,
                message=plan.message,
                warnings=session.warnings,
            )

        action = plan.action
        assert action is not None  # guaranteed by PlannerDecision validation

        # --- RISK GATE (deterministic, never model-controlled) ------------
        element = page.by_id().get(action.target or "")
        risk = classify_risk(
            action.model_dump(),
            element.model_dump() if element else None,
            domain=understanding.domain or "this website",
            current_url=page.url,
        )

        session.step += 1

        if risk.requires_confirmation:
            session.pending_action = action
            session.pending_risk = risk.to_dict()
            session.state = WAITING_CONFIRMATION
            self.store.update_task(session.task_id, status=WAITING_CONFIRMATION)
            self.sessions.save(session)
            return self._confirm_directive(session, activity=plan.activity)

        session.state = EXECUTING
        self.store.update_task(session.task_id, status=EXECUTING)
        self.sessions.save(session)
        return Directive(
            type="action",
            task_id=session.task_id,
            state=EXECUTING,
            activity=plan.activity,
            step=session.step,
            max_steps=self.max_steps,
            action=action.model_dump(),
            risk=risk.to_dict(),
            warnings=session.warnings,
        )

    # -- helpers ----------------------------------------------------------

    def _confirm_directive(self, session: Session, activity: str = "") -> Directive:
        action = session.pending_action
        return Directive(
            type="confirm",
            task_id=session.task_id,
            state=WAITING_CONFIRMATION,
            activity=activity or "Awaiting confirmation",
            step=session.step,
            max_steps=self.max_steps,
            action=action.model_dump() if action else None,
            risk=session.pending_risk,
            message=(session.pending_risk or {}).get("explanation", ""),
            warnings=session.warnings,
        )

    def _record_result(self, session: Session, result: dict[str, Any]) -> None:
        """Append an executed step to history and persist it."""
        last_action: dict[str, Any] = {}
        if session.history and "result" not in session.history[-1]:
            last_action = session.history[-1].get("action", {})

        action = result.get("action") or last_action.get("action") or "UNKNOWN"
        entry = {
            "step": session.step,
            "action": {
                "action": action,
                "target": result.get("target"),
                "value": last_action.get("value"),
            },
            "result": {
                "success": bool(result.get("success")),
                "error": result.get("error"),
                "url_changed": bool(result.get("url_changed")),
                "page_changed": bool(result.get("page_changed")),
            },
        }
        session.history.append(entry)

        data = result.get("data")
        if result.get("success") and data:
            rendered = data if isinstance(data, str) else _stringify(data)
            if rendered.strip():
                session.extracted.append(rendered[: settings.max_extract_chars])

        self.store.record_action(
            session.task_id,
            session.step,
            entry["action"],
            entry["result"],
            "SUCCESS" if result.get("success") else "FAILED",
        )

    def _terminal(self, session: Session, state: str, message: str) -> Directive:
        session.state = state
        # The task is over: its live state is no longer needed by anyone.
        self.sessions.delete(session.task_id)
        self.store.update_task(
            session.task_id,
            status=state,
            summary=message[:2000],
            current_url=session.current_url,
            completed=True,
            error=message[:2000] if state == FAILED else None,
        )
        directive = Directive(
            type="answer" if state == COMPLETED else "error",
            task_id=session.task_id,
            state=state,
            activity={
                COMPLETED: "Task completed",
                FAILED: "Task failed",
                CANCELLED: "Task cancelled",
            }[state],
            step=session.step,
            max_steps=self.max_steps,
            message=message,
            warnings=session.warnings,
        )
        return directive


def _failure_message(result: dict[str, Any], attempts: int) -> str:
    error = result.get("error") or "the action did not succeed"
    return (
        f"I could not complete the task: {error}. "
        f"I retried {attempts - 1} time(s) after refreshing the page. "
        "The site may have changed, or the element may need a different approach."
    )


def _stringify(data: Any) -> str:
    import json

    try:
        return json.dumps(data, ensure_ascii=False)[: settings.max_extract_chars]
    except (TypeError, ValueError):
        return str(data)[: settings.max_extract_chars]
