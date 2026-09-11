"""Google OAuth verification for a hosted SurfAI backend.

The Chrome extension obtains a Google access token through
`chrome.identity.getAuthToken` and sends it as a bearer token. This module is
the other half: it verifies the token *with Google* on every cache miss, and
derives the SurfAI user id from the verified subject.

Why verify server-side rather than trust a JWT signature locally
---------------------------------------------------------------
`chrome.identity.getAuthToken` returns an OAuth **access token**, not a signed
ID token, so there is nothing to verify offline. Google's tokeninfo endpoint is
the authority on whether that token is live, who it belongs to, and which client
it was issued for. Checking `aud` against our own client id is what stops a
token minted for some other application being replayed here.

Verified results are cached briefly so a multi-step task does not make one
network round trip per request. The cache is keyed by a hash of the token, never
the token itself, so a memory dump or log of the cache leaks nothing usable.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

import httpx
from fastapi import Request

from app.config import settings
from app.security.permissions import AuthProvider, User

logger = logging.getLogger(__name__)

TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"

# Google tokens live for about an hour. A short cache removes the per-request
# round trip without meaningfully extending the life of a revoked token.
CACHE_TTL_SECONDS = 300
MAX_CACHE_ENTRIES = 2048


@dataclass(frozen=True)
class _CachedIdentity:
    user: User
    expires_at: float


def _fingerprint(token: str) -> str:
    """A stable key for a token that is not the token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class GoogleAuthProvider(AuthProvider):
    """Authenticates a request from a Google OAuth access token."""

    name = "google"

    def __init__(
        self,
        client_id: str | None = None,
        allowed_emails: set[str] | None = None,
    ) -> None:
        self.client_id = client_id if client_id is not None else settings.google_client_id
        self.allowed_emails = (
            allowed_emails if allowed_emails is not None else settings.allowed_email_set
        )
        self._cache: dict[str, _CachedIdentity] = {}
        self._client: httpx.AsyncClient | None = None

        if not self.client_id:
            # Failing loudly at construction beats every request returning 401
            # with no explanation of why.
            raise ValueError(
                "AUTH_PROVIDER=google requires GOOGLE_CLIENT_ID to be set. It is the "
                "OAuth client id the extension authenticates with, and is checked "
                "against the token's audience."
            )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    @staticmethod
    def _bearer_token(request: Request) -> str | None:
        header = request.headers.get("authorization") or ""
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return None
        return token.strip()

    def _cached(self, fingerprint: str) -> User | None:
        entry = self._cache.get(fingerprint)
        if entry is None:
            return None
        if entry.expires_at <= time.monotonic():
            self._cache.pop(fingerprint, None)
            return None
        return entry.user

    def _remember(self, fingerprint: str, user: User, ttl: float) -> None:
        if len(self._cache) >= MAX_CACHE_ENTRIES:
            # Cheap eviction: drop everything already expired, then the oldest.
            now = time.monotonic()
            for key in [k for k, v in self._cache.items() if v.expires_at <= now]:
                self._cache.pop(key, None)
            if len(self._cache) >= MAX_CACHE_ENTRIES:
                oldest = min(self._cache, key=lambda k: self._cache[k].expires_at)
                self._cache.pop(oldest, None)
        self._cache[fingerprint] = _CachedIdentity(user, time.monotonic() + ttl)

    async def authenticate(self, request: Request) -> User | None:
        token = self._bearer_token(request)
        if token is None:
            return None

        fingerprint = _fingerprint(token)
        cached = self._cached(fingerprint)
        if cached is not None:
            return cached

        try:
            client = await self._get_client()
            response = await client.get(TOKENINFO_URL, params={"access_token": token})
        except httpx.HTTPError as exc:
            # Reaching Google failed. Refuse rather than fall open.
            logger.warning("Could not verify token with Google: %s", exc)
            return None

        if response.status_code != 200:
            logger.info("Google rejected a token (HTTP %s)", response.status_code)
            return None

        try:
            info = response.json()
        except ValueError:
            logger.warning("Google tokeninfo returned a non-JSON body")
            return None

        # The audience check is the important one: without it, a token issued to
        # any other Google OAuth client would be accepted here.
        audience = info.get("aud") or info.get("azp")
        if audience != self.client_id:
            logger.warning("Rejected a token issued for a different client")
            return None

        subject = str(info.get("sub") or "").strip()
        if not subject:
            logger.warning("Google token carried no subject")
            return None

        email = str(info.get("email") or "").strip().lower()
        if self.allowed_emails and email not in self.allowed_emails:
            logger.info("Rejected a verified user outside the allowlist")
            return None

        user = User(
            # `sub` is Google's stable, non-reassignable id. Email is not: it can
            # change, so it is never the primary key for a user's data.
            id=f"google:{subject}",
            display_name=email or "Google user",
            provider=self.name,
        )

        try:
            remaining = float(info.get("expires_in", CACHE_TTL_SECONDS))
        except (TypeError, ValueError):
            remaining = CACHE_TTL_SECONDS

        self._remember(fingerprint, user, min(CACHE_TTL_SECONDS, max(remaining, 0.0)))
        return user
