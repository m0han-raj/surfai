"""Schema bootstrap for a fresh deployment.

Alembic owns migrations, but it is excluded from the serverless bundle to stay
inside the lambda size limit, and a managed provider's credentials never leave
the platform, so a fresh hosted deployment has nowhere natural to run
``alembic upgrade head``. Every request then fails on tables that do not exist.

This covers exactly that gap and nothing more:

* an **empty** database gets the current schema, stamped with the revision it
  corresponds to, so Alembic can take over from there;
* a database that already has an ``alembic_version`` row is left completely
  alone, even if it is behind. Guessing at an upgrade is how data gets lost;
  applying one is Alembic's job and it says so in the log.

An advisory lock makes it safe for two cold starts to race. Postgres holds it
for the session, so a crash releases it rather than deadlocking the next boot.
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect, text

from app.database.database import get_engine
from app.database.models import Base

logger = logging.getLogger(__name__)

# The migration this schema corresponds to. Kept in step with
# migrations/versions by test_schema_revision_matches_the_latest_migration,
# because a stale value here would stamp a fresh database with a revision it
# does not actually have and every later migration would be skipped.
SCHEMA_REVISION = "0002"

# Any stable 64-bit constant. Derived from the project name so it cannot
# collide with a lock another application on the same database takes.
_ADVISORY_LOCK_KEY = 0x5507_A100_0001


def _current_revision(connection) -> str | None:  # noqa: ANN001
    row = connection.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).first()
    return row[0] if row else None


def ensure_schema() -> bool:
    """Create the schema if the database is empty. Returns True if it did.

    Never raises: a failure here should leave the app reporting a degraded
    database rather than refusing to boot, since `/health` is what tells the
    operator what went wrong.
    """
    try:
        engine = get_engine()
        if engine.dialect.name != "postgresql":
            # SQLite development and tests create tables directly; there is no
            # concurrency to guard against and no advisory lock to take.
            return False

        with engine.begin() as connection:
            # Blocks rather than skipping: a second cold start should wait and
            # then observe the finished schema, not proceed without one.
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}
            )

            tables = set(inspect(connection).get_table_names())

            if "alembic_version" in tables:
                revision = _current_revision(connection)
                if revision != SCHEMA_REVISION:
                    logger.warning(
                        "Database is at revision %s, application expects %s. "
                        "Run `alembic upgrade head` against it; nothing was changed here.",
                        revision,
                        SCHEMA_REVISION,
                    )
                return False

            if tables & {"favourites", "tasks", "task_actions"}:
                # Tables without a version row: something created them outside
                # Alembic. Adding more is not this function's decision.
                logger.warning(
                    "Found application tables with no alembic_version row. "
                    "Leaving the schema untouched; stamp it with `alembic stamp head`."
                )
                return False

            logger.info("Empty database: creating schema at revision %s", SCHEMA_REVISION)
            Base.metadata.create_all(bind=connection)
            connection.execute(
                text("CREATE TABLE IF NOT EXISTS alembic_version ("
                     "version_num VARCHAR(32) NOT NULL, "
                     "CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num))")
            )
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:rev)"),
                {"rev": SCHEMA_REVISION},
            )
            return True

    except Exception:  # noqa: BLE001
        logger.exception("Could not ensure the database schema")
        return False
