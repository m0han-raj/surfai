"""Connection string resolution.

Managed providers hand out URLs that SQLAlchemy cannot use as given: no driver
prefix, and under whichever environment variable that provider happens to
choose. Getting this wrong produces a ModuleNotFoundError naming psycopg2, a
package nobody asked for, which sends you looking in entirely the wrong place.

Every case constructs Settings with `_env_file=None`; otherwise the developer's
own .env decides the answer and the tests prove nothing.
"""

from __future__ import annotations

import pytest

from app.config import Settings, _with_psycopg_driver

LOCAL_DEFAULT = "postgresql+psycopg://surfai:surfai@localhost:5432/surfai"


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


# --- driver normalisation -------------------------------------------------


@pytest.mark.parametrize(
    "given,expected",
    [
        # What Neon, Supabase and Railway actually print in their dashboards.
        ("postgres://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("postgresql://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        (
            "postgres://u:p@ep-cool-pooler.aws.neon.tech/neondb?sslmode=require",
            "postgresql+psycopg://u:p@ep-cool-pooler.aws.neon.tech/neondb?sslmode=require",
        ),
        # Already correct, or not PostgreSQL at all: left alone.
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("sqlite:///./local.db", "sqlite:///./local.db"),
        ("", ""),
    ],
)
def test_driver_normalisation(given: str, expected: str) -> None:
    assert _with_psycopg_driver(given) == expected


def test_query_parameters_survive() -> None:
    """Providers append sslmode and similar; dropping them breaks the connect."""
    url = _with_psycopg_driver("postgres://u:p@host/db?sslmode=require&connect_timeout=10")
    assert url.endswith("?sslmode=require&connect_timeout=10")


def test_a_password_containing_a_scheme_like_string_is_not_mangled() -> None:
    url = _with_psycopg_driver("postgres://user:postgres://weird@host/db")
    assert url == "postgresql+psycopg://user:postgres://weird@host/db"


# --- picking a variable ---------------------------------------------------


def test_an_explicit_database_url_wins() -> None:
    s = settings(database_url="postgresql://explicit/db", postgres_url="postgres://fallback/db")
    assert "explicit" in s.database_url
    assert "fallback" not in s.database_url


@pytest.mark.parametrize(
    "field",
    ["postgres_url", "postgres_prisma_url", "database_url_unpooled", "postgres_url_non_pooling"],
)
def test_each_provider_variable_is_read(field: str) -> None:
    s = settings(**{field: "postgres://provider/db"})
    assert s.database_url == "postgresql+psycopg://provider/db"


def test_a_pooled_url_is_preferred_over_a_direct_one() -> None:
    """A serverless host opens a connection per invocation; pooling matters."""
    s = settings(
        postgres_url="postgres://pooled/db",
        postgres_url_non_pooling="postgres://direct/db",
    )
    assert "pooled" in s.database_url
    assert "direct" not in s.database_url


def test_no_configuration_falls_back_to_the_local_default() -> None:
    assert settings().database_url == LOCAL_DEFAULT


def test_the_local_default_already_names_the_driver() -> None:
    """Whatever the default is, it must not resolve to psycopg2."""
    assert settings().database_url.startswith("postgresql+psycopg://")


# --- serverless detection -------------------------------------------------


def test_serverless_is_detected_from_the_platform_variable() -> None:
    assert settings(vercel="1").is_serverless is True
    assert settings().is_serverless is False


def test_local_auth_is_the_default_and_google_is_not() -> None:
    assert settings().is_local_auth is True
    assert settings(auth_provider="google").is_local_auth is False


def test_the_allowlist_is_parsed_and_normalised() -> None:
    s = settings(allowed_emails=" One@Example.com , two@example.com ,, ")
    assert s.allowed_email_set == {"one@example.com", "two@example.com"}


def test_an_empty_allowlist_admits_everyone() -> None:
    """Empty means "any verified account", not "nobody"."""
    assert settings(allowed_emails="").allowed_email_set == set()
