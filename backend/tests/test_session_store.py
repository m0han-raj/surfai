"""Agent session persistence.

The property under test is that a task can be continued by a process that did
not start it. That is what allows more than one backend instance, and what stops
a deploy from stranding every task in flight.

The database store is exercised against real SQLite rather than a mock, because
the thing most likely to break is serialisation, and a mock would not catch it.
"""

from __future__ import annotations

from datetime import UTC

import pytest

from app.agents.orchestrator import (
    CANCELLED,
    COMPLETED,
    MemorySessionStore,
    Orchestrator,
    Session,
)
from app.browser.action_schema import BrowserAction
from app.database.repositories.sessions import DatabaseSessionStore


def action(action_type: str, activity: str = "Working", **fields) -> dict:
    return {"type": "action", "activity": activity, "action": {"action": action_type, **fields}}


def answer(message: str) -> dict:
    return {"type": "answer", "message": message}


def ok(action_type: str, target: str | None = None, **fields) -> dict:
    return {
        "success": True,
        "action": action_type,
        "target": target,
        "url_changed": False,
        "page_changed": True,
        **fields,
    }


# --- serialisation ---------------------------------------------------------


def test_a_session_round_trips_through_a_dict() -> None:
    original = Session(
        task_id="t1",
        goal="find something",
        user_message="find something",
        user_id="alice",
        state="EXECUTING",
        step=3,
        consecutive_failures=1,
        history=[{"step": 1, "action": {"action": "CLICK"}, "result": {"success": True}}],
        tools=[{"name": "search"}],
        favourite={"id": "f1", "name": "Saved"},
        extracted=["some text"],
        pending_action=BrowserAction(action="CLICK", target="e6"),
        pending_risk={"level": "HIGH", "category": "PURCHASE"},
        current_url="https://example.com/",
        warnings=["a warning"],
    )

    restored = Session.from_dict(original.to_dict())

    assert restored.task_id == "t1"
    assert restored.user_id == "alice"
    assert restored.step == 3
    assert restored.consecutive_failures == 1
    assert restored.history == original.history
    assert restored.tools == original.tools
    assert restored.favourite == original.favourite
    assert restored.extracted == original.extracted
    assert restored.pending_action.target == "e6"
    assert restored.pending_risk["level"] == "HIGH"
    assert restored.current_url == "https://example.com/"
    assert restored.warnings == ["a warning"]


def test_an_unparseable_pending_action_is_dropped_not_trusted() -> None:
    """A stored action from an older schema must not bypass validation."""
    data = Session(task_id="t1", goal="g", user_message="m").to_dict()
    data["pending_action"] = {"action": "EXECUTE_SCRIPT", "value": "alert(1)"}

    restored = Session.from_dict(data)

    assert restored.pending_action is None


def test_elapsed_is_measured_in_wall_clock() -> None:
    """A monotonic counter is meaningless once another process reads it."""
    import time

    session = Session(task_id="t1", goal="g", user_message="m", started_at=time.time() - 30)
    assert 29 <= session.elapsed <= 40


# --- the database store ----------------------------------------------------


@pytest.fixture
def db_store(database):
    return DatabaseSessionStore()


def _seed_task(task_id: str, user_id: str) -> None:
    """Sessions reference tasks, so the row has to exist first."""
    from app.database.database import session_scope
    from app.database.repositories.tasks import TaskRepository

    with session_scope() as db:
        TaskRepository(db, user_id).create(task_id=task_id, request="test")


def test_a_saved_session_can_be_loaded_back(db_store) -> None:
    _seed_task("t1", "alice")
    db_store.save(Session(task_id="t1", goal="a goal", user_message="m", user_id="alice", step=2))

    restored = db_store.load("t1", "alice")

    assert restored is not None
    assert restored.goal == "a goal"
    assert restored.step == 2


def test_loading_an_unknown_session_returns_none(db_store) -> None:
    assert db_store.load("never-existed", "alice") is None


def test_saving_twice_updates_rather_than_duplicates(db_store) -> None:
    _seed_task("t1", "alice")
    db_store.save(Session(task_id="t1", goal="g", user_message="m", user_id="alice", step=1))
    db_store.save(Session(task_id="t1", goal="g", user_message="m", user_id="alice", step=7))

    assert db_store.load("t1", "alice").step == 7


def test_a_deleted_session_is_gone(db_store) -> None:
    _seed_task("t1", "alice")
    db_store.save(Session(task_id="t1", goal="g", user_message="m", user_id="alice"))
    db_store.delete("t1")

    assert db_store.load("t1", "alice") is None


def test_deleting_an_unknown_session_is_safe(db_store) -> None:
    db_store.delete("never-existed")


def test_another_user_cannot_load_your_session(db_store) -> None:
    """Knowing a task id must not be enough to drive someone else's agent."""
    _seed_task("t1", "alice")
    db_store.save(Session(task_id="t1", goal="private", user_message="m", user_id="alice"))

    assert db_store.load("t1", "bob") is None
    assert db_store.load("t1", "alice") is not None


def test_stale_sessions_are_purged(db_store) -> None:
    from datetime import datetime, timedelta

    from app.database.database import session_scope
    from app.database.models import AgentSession

    _seed_task("t1", "alice")
    db_store.save(Session(task_id="t1", goal="g", user_message="m", user_id="alice"))

    with session_scope() as db:
        row = db.get(AgentSession, "t1")
        row.updated_at = datetime.now(UTC) - timedelta(hours=5)

    assert db_store.purge_stale(older_than_seconds=3600) == 1
    assert db_store.load("t1", "alice") is None


def test_a_fresh_session_survives_a_purge(db_store) -> None:
    _seed_task("t1", "alice")
    db_store.save(Session(task_id="t1", goal="g", user_message="m", user_id="alice"))

    assert db_store.purge_stale(older_than_seconds=3600) == 0
    assert db_store.load("t1", "alice") is not None


# --- the property that justifies all of it ---------------------------------


async def test_a_task_can_be_continued_by_a_different_orchestrator(
    fake_llm, product_page, database
) -> None:
    """This is the whole point: two instances, one task.

    The second orchestrator shares only the database. If anything the loop
    needs still lived in the first one's memory, this would fail.
    """
    store = DatabaseSessionStore()
    _seed_task("t1", "alice")

    first = Orchestrator(fake_llm, session_store=store)
    fake_llm.push(action("TYPE", target="e1", value="hello"))
    directive = await first.start(
        task_id="t1", message="do something", page=product_page, user_id="alice"
    )
    assert directive.action["action"] == "TYPE"

    # A different instance, with no shared memory whatsoever.
    second = Orchestrator(fake_llm, session_store=DatabaseSessionStore())
    fake_llm.push(answer("Finished on the other instance."))

    directive = await second.continue_task(
        task_id="t1", page=product_page, result=ok("TYPE", "e1"), user_id="alice"
    )

    assert directive.state == COMPLETED
    assert directive.message == "Finished on the other instance."


async def test_a_pending_confirmation_survives_an_instance_change(
    fake_llm, product_page, database
) -> None:
    """The most fragile state to lose: an action awaiting human approval."""
    store = DatabaseSessionStore()
    _seed_task("t1", "alice")

    first = Orchestrator(fake_llm, session_store=store)
    fake_llm.push(action("CLICK", "Buying", target="e6"))  # "Buy now"
    directive = await first.start(
        task_id="t1", message="buy it", page=product_page, user_id="alice"
    )
    assert directive.type == "confirm"

    second = Orchestrator(fake_llm, session_store=DatabaseSessionStore())
    directive = await second.continue_task(
        task_id="t1", page=product_page, confirmation=True, user_id="alice"
    )

    # The approved action is released by an instance that never planned it.
    assert directive.type == "action"
    assert directive.action["target"] == "e6"


async def test_another_user_cannot_continue_your_task(
    fake_llm, product_page, database
) -> None:
    _seed_task("t1", "alice")
    orchestrator = Orchestrator(fake_llm, session_store=DatabaseSessionStore())
    fake_llm.push(action("TYPE", target="e1", value="x"))
    await orchestrator.start(
        task_id="t1", message="do something", page=product_page, user_id="alice"
    )

    directive = await orchestrator.continue_task(
        task_id="t1", page=product_page, result=ok("TYPE", "e1"), user_id="bob"
    )

    assert directive.type == "error"
    assert directive.state != COMPLETED


async def test_a_finished_task_leaves_no_session_behind(
    fake_llm, product_page, database
) -> None:
    store = DatabaseSessionStore()
    _seed_task("t1", "alice")
    orchestrator = Orchestrator(fake_llm, session_store=store)

    fake_llm.push(action("EXTRACT"), answer("done"))
    await orchestrator.start(task_id="t1", message="read it", page=product_page, user_id="alice")
    await orchestrator.continue_task(
        task_id="t1", page=product_page, result=ok("EXTRACT"), user_id="alice"
    )

    assert store.load("t1", "alice") is None


async def test_a_cancelled_task_leaves_no_session_behind(
    fake_llm, product_page, database
) -> None:
    store = DatabaseSessionStore()
    _seed_task("t1", "alice")
    orchestrator = Orchestrator(fake_llm, session_store=store)

    fake_llm.push(action("TYPE", target="e1", value="x"))
    await orchestrator.start(task_id="t1", message="go", page=product_page, user_id="alice")

    directive = orchestrator.cancel("t1", "alice")

    assert directive.state == CANCELLED
    assert store.load("t1", "alice") is None


# --- the in-memory store keeps the same contract ---------------------------


def test_the_memory_store_also_scopes_by_user() -> None:
    store = MemorySessionStore()
    store.save(Session(task_id="t1", goal="g", user_message="m", user_id="alice"))

    assert store.load("t1", "bob") is None
    assert store.load("t1", "alice") is not None
    # No user context (single local user) still works.
    assert store.load("t1", "") is not None
