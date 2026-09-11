"""Schema bootstrap.

The dangerous failure here is not "did not create tables"; it is "changed a
database it should not have touched". Most of these tests are about what
bootstrap declines to do.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import inspect, text

from app.database.bootstrap import SCHEMA_REVISION, ensure_schema
from app.database.database import get_engine

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def test_schema_revision_matches_the_latest_migration() -> None:
    """The stamped revision has to be real, and has to be the newest one.

    A stale constant stamps a fresh database with a revision it does not have,
    and every migration after that point is then silently skipped forever.
    """
    revisions: dict[str, str | None] = {}
    for path in MIGRATIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r'^revision: str = ["\']([^"\']+)["\']', source, re.MULTILINE)
        down = re.search(
            r'^down_revision: [^=]+= ["\']?([^"\'\n]+)["\']?', source, re.MULTILINE
        )
        if revision:
            value = down.group(1).strip() if down else None
            revisions[revision.group(1)] = None if value in (None, "None") else value

    assert revisions, "no migrations found"
    assert SCHEMA_REVISION in revisions, (
        f"SCHEMA_REVISION is {SCHEMA_REVISION!r}, which is not a real migration. "
        f"Known revisions: {sorted(revisions)}"
    )

    # The head is the revision nothing else points back to.
    parents = {down for down in revisions.values() if down}
    heads = set(revisions) - parents
    assert heads == {SCHEMA_REVISION}, (
        f"SCHEMA_REVISION is {SCHEMA_REVISION!r} but the migration head is {heads}. "
        "Update app/database/bootstrap.py when adding a migration."
    )


# --- behaviour on SQLite ---------------------------------------------------


def test_it_declines_to_act_on_sqlite(database) -> None:
    """Development and tests create tables directly; there is nothing to guard."""
    assert ensure_schema() is False


def test_a_failure_is_reported_rather_than_raised(monkeypatch) -> None:
    """A bad bootstrap must leave /health to explain it, not block startup."""
    from app.database import bootstrap

    def explode() -> None:
        raise RuntimeError("no database")

    monkeypatch.setattr(bootstrap, "get_engine", explode)
    assert bootstrap.ensure_schema() is False


# --- behaviour on PostgreSQL ----------------------------------------------
#
# Skipped unless a real PostgreSQL is reachable: the advisory lock, the
# information schema and the DDL paths are all dialect-specific, so SQLite
# would prove nothing about the code that actually runs in production.

pytestmark = []


@pytest.fixture
def postgres(monkeypatch):
    import os

    url = os.environ.get("TEST_POSTGRES_URL")
    if not url:
        pytest.skip("set TEST_POSTGRES_URL to exercise the PostgreSQL paths")

    # This fixture drops every application table, so it must never be pointed
    # at a database anyone cares about. Requiring the name to say so is a
    # cheap contract: it already cost one local development database.
    name = url.rsplit("/", 1)[-1].split("?")[0]
    if "test" not in name.lower():
        pytest.fail(
            f"TEST_POSTGRES_URL points at a database named {name!r}. These tests "
            "drop every application table; use a scratch database whose name "
            "contains 'test'."
        )

    from app.database import database

    database.configure_engine(url)
    engine = get_engine()
    with engine.begin() as conn:
        for table in ("agent_sessions", "task_actions", "tasks", "favourites", "alembic_version"):
            conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    yield engine
    with engine.begin() as conn:
        for table in ("agent_sessions", "task_actions", "tasks", "favourites", "alembic_version"):
            conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
    database.dispose_engine()


def test_an_empty_database_gets_the_schema(postgres) -> None:
    assert ensure_schema() is True

    tables = set(inspect(postgres).get_table_names())
    assert {"favourites", "tasks", "task_actions", "agent_sessions"} <= tables

    with postgres.begin() as conn:
        stamped = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert stamped == SCHEMA_REVISION


def test_running_it_twice_changes_nothing(postgres) -> None:
    assert ensure_schema() is True
    assert ensure_schema() is False, "a second run must be a no-op"

    with postgres.begin() as conn:
        rows = conn.execute(text("SELECT count(*) FROM alembic_version")).scalar()
    assert rows == 1, "the version row was duplicated"


def test_a_database_at_an_older_revision_is_left_alone(postgres, caplog) -> None:
    """Guessing at an upgrade is how data gets lost. Alembic's job, not this."""
    with postgres.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL, "
                "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))"
            )
        )
        conn.execute(text("INSERT INTO alembic_version VALUES ('0001')"))

    assert ensure_schema() is False
    assert "alembic upgrade head" in caplog.text

    tables = set(inspect(postgres).get_table_names())
    assert "agent_sessions" not in tables, "it created tables for a migration it did not apply"


def test_tables_without_a_version_row_are_left_alone(postgres, caplog) -> None:
    with postgres.begin() as conn:
        conn.execute(text("CREATE TABLE favourites (id VARCHAR(36) PRIMARY KEY)"))

    assert ensure_schema() is False
    assert "alembic stamp head" in caplog.text

    # The pre-existing table keeps its own shape.
    columns = {c["name"] for c in inspect(postgres).get_columns("favourites")}
    assert columns == {"id"}
