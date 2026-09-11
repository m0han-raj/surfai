"""Authentication and per-user isolation.

Two separate concerns are tested here:

* the Google provider verifies tokens properly, and fails closed on every path
  where it cannot prove who the caller is;
* one user's data is unreachable by another, which is what makes a hosted
  deployment safe. That property is enforced by the repositories, so it is
  tested through the API rather than by inspecting queries.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import Request

from app.security.google_auth import GoogleAuthProvider
from app.security.permissions import (
    LocalAuthProvider,
    User,
    _build_provider,
    set_auth_provider,
)

CLIENT_ID = "1234.apps.googleusercontent.com"


def make_request(headers: dict[str, str] | None = None) -> Request:
    """A bare ASGI request carrying only the headers under test."""
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": raw})


def provider_with(handler, **kwargs) -> GoogleAuthProvider:
    provider = GoogleAuthProvider(client_id=CLIENT_ID, **kwargs)
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


def tokeninfo(**overrides) -> httpx.Response:
    body = {
        "aud": CLIENT_ID,
        "sub": "110000000000000000001",
        "email": "person@example.com",
        "expires_in": "3599",
        **overrides,
    }
    return httpx.Response(200, json=body)


# --- provider selection ---------------------------------------------------


def test_local_is_the_default() -> None:
    assert isinstance(_build_provider(""), LocalAuthProvider)
    assert isinstance(_build_provider("local"), LocalAuthProvider)


def test_unknown_provider_fails_loudly() -> None:
    """Silently falling back to unauthenticated would be the worst outcome."""
    with pytest.raises(ValueError, match="Unknown AUTH_PROVIDER"):
        _build_provider("kerberos")


def test_google_requires_a_client_id() -> None:
    with pytest.raises(ValueError, match="GOOGLE_CLIENT_ID"):
        GoogleAuthProvider(client_id="")


# --- accepting a valid token ----------------------------------------------


async def test_valid_token_is_accepted() -> None:
    provider = provider_with(lambda request: tokeninfo())
    user = await provider.authenticate(make_request({"Authorization": "Bearer good-token"}))

    assert user is not None
    # The stable Google subject is the identity, not the email.
    assert user.id == "google:110000000000000000001"
    assert user.provider == "google"


async def test_identity_is_the_subject_not_the_email() -> None:
    """Emails can be reassigned; `sub` cannot. Data must key off `sub`."""
    provider = provider_with(lambda request: tokeninfo(email="renamed@example.com"))
    user = await provider.authenticate(make_request({"Authorization": "Bearer t"}))
    assert user.id == "google:110000000000000000001"


async def test_verification_is_cached() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url)
        return tokeninfo()

    provider = provider_with(handler)
    headers = {"Authorization": "Bearer repeated"}
    await provider.authenticate(make_request(headers))
    await provider.authenticate(make_request(headers))
    await provider.authenticate(make_request(headers))

    assert len(calls) == 1, "a multi-step task should not verify once per request"


async def test_the_cache_is_not_keyed_by_the_token() -> None:
    """A dump of the cache must not hand out usable tokens."""
    provider = provider_with(lambda request: tokeninfo())
    await provider.authenticate(make_request({"Authorization": "Bearer secret-value"}))

    assert "secret-value" not in str(provider._cache.keys())


async def test_different_tokens_are_cached_separately() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("access_token")
        return tokeninfo(sub="111" if token == "a" else "222")

    provider = provider_with(handler)
    first = await provider.authenticate(make_request({"Authorization": "Bearer a"}))
    second = await provider.authenticate(make_request({"Authorization": "Bearer b"}))

    assert first.id != second.id


# --- failing closed --------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic abc123"},
        {"Authorization": "token abc123"},
    ],
)
async def test_a_missing_or_malformed_header_is_rejected(headers: dict) -> None:
    provider = provider_with(lambda request: tokeninfo())
    assert await provider.authenticate(make_request(headers)) is None


async def test_a_token_for_another_client_is_rejected() -> None:
    """Without this check, any Google OAuth token would authenticate here."""
    provider = provider_with(lambda request: tokeninfo(aud="9999.apps.googleusercontent.com"))
    assert await provider.authenticate(make_request({"Authorization": "Bearer t"})) is None


async def test_a_token_google_rejects_is_rejected() -> None:
    provider = provider_with(lambda request: httpx.Response(400, json={"error": "invalid"}))
    assert await provider.authenticate(make_request({"Authorization": "Bearer t"})) is None


async def test_a_token_without_a_subject_is_rejected() -> None:
    provider = provider_with(lambda request: tokeninfo(sub=""))
    assert await provider.authenticate(make_request({"Authorization": "Bearer t"})) is None


async def test_it_fails_closed_when_google_is_unreachable() -> None:
    """Refusing beats falling open when the authority cannot be consulted."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    provider = provider_with(handler)
    assert await provider.authenticate(make_request({"Authorization": "Bearer t"})) is None


async def test_a_non_json_response_is_rejected() -> None:
    provider = provider_with(lambda request: httpx.Response(200, text="<html>oops</html>"))
    assert await provider.authenticate(make_request({"Authorization": "Bearer t"})) is None


# --- allowlist -------------------------------------------------------------


async def test_the_allowlist_admits_a_listed_address() -> None:
    provider = provider_with(
        lambda request: tokeninfo(), allowed_emails={"person@example.com"}
    )
    assert await provider.authenticate(make_request({"Authorization": "Bearer t"})) is not None


async def test_the_allowlist_rejects_an_unlisted_address() -> None:
    provider = provider_with(
        lambda request: tokeninfo(email="stranger@example.com"),
        allowed_emails={"person@example.com"},
    )
    assert await provider.authenticate(make_request({"Authorization": "Bearer t"})) is None


# --- per-user isolation through the API ------------------------------------


class _FixedUser(LocalAuthProvider):
    """Authenticates every request as one chosen user."""

    def __init__(self, user_id: str) -> None:
        super().__init__(user_id)

    async def authenticate(self, request: Request) -> User:
        return User(id=self.user_id, display_name=self.user_id, provider="test")


@pytest.fixture
def as_user():
    """Switch the authenticated identity mid-test."""

    def _switch(user_id: str) -> None:
        set_auth_provider(_FixedUser(user_id))

    yield _switch
    set_auth_provider(None)


def test_one_user_cannot_read_anothers_favourites(client, as_user) -> None:
    as_user("alice")
    created = client.post(
        "/api/favourites",
        json={"name": "Alice private", "url": "https://example.com/a"},
    ).json()

    as_user("bob")
    assert client.get("/api/favourites").json() == []
    assert client.get(f"/api/favourites/{created['id']}").status_code == 404


def test_one_user_cannot_modify_anothers_favourites(client, as_user) -> None:
    as_user("alice")
    created = client.post(
        "/api/favourites", json={"name": "Alice", "url": "https://example.com/a"}
    ).json()

    as_user("bob")
    assert client.put(f"/api/favourites/{created['id']}", json={"name": "Bob"}).status_code == 404
    assert client.delete(f"/api/favourites/{created['id']}").status_code == 404

    # Alice's record is untouched.
    as_user("alice")
    assert client.get(f"/api/favourites/{created['id']}").json()["name"] == "Alice"


def test_one_user_cannot_read_anothers_tasks(client, as_user, fake_llm, product_page) -> None:
    as_user("alice")
    fake_llm.push(
        {"intent": "browse_task"},
        {"type": "answer", "message": "done"},
    )
    task_id = client.post(
        "/api/tasks", json={"request": "alice task", "page_context": product_page}
    ).json()["task_id"]

    as_user("bob")
    assert client.get("/api/tasks").json()["total"] == 0
    assert client.get(f"/api/tasks/{task_id}").status_code == 404
    assert client.delete(f"/api/tasks/{task_id}").status_code == 404


def test_favourite_resolution_only_searches_your_own(client, as_user) -> None:
    as_user("alice")
    client.post(
        "/api/favourites",
        json={"name": "Quarterly Review", "url": "https://example.com/q", "intent": "reports"},
    )

    as_user("bob")
    body = client.post(
        "/api/favourites/resolve", json={"query": "open my quarterly review"}
    ).json()
    assert body["found"] is False
