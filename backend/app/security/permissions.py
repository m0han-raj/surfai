"""Identity and permissions.

The MVP runs as a single local user, but authentication is kept behind an
`AuthProvider` interface so adding Google/GitHub OAuth or email login later is a
new provider plus a settings change -- not a rewrite of every endpoint. Every
route already resolves its user through `get_current_user`, so the data model
and queries are multi-user ready today.

No passwords are stored, hashed or otherwise, anywhere in this codebase.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass

from fastapi import Request

from app.config import settings


@dataclass(frozen=True)
class User:
    id: str
    display_name: str = "Local user"
    provider: str = "local"
    is_authenticated: bool = True


class AuthProvider(abc.ABC):
    """Resolves a request to a user."""

    name: str = "abstract"

    @abc.abstractmethod
    async def authenticate(self, request: Request) -> User | None:
        """Return the user for this request, or None to reject it."""

    async def logout(self, request: Request) -> None:  # pragma: no cover - default no-op
        return None


class LocalAuthProvider(AuthProvider):
    """Single-user mode.

    Intended for a backend bound to localhost. It grants a fixed identity to
    every request -- which is exactly right for a personal tool on your own
    machine, and exactly wrong for a shared deployment. `SECURITY.md` documents
    the boundary.
    """

    name = "local"

    def __init__(self, user_id: str | None = None) -> None:
        self.user_id = user_id or settings.local_user_id

    async def authenticate(self, request: Request) -> User:
        return User(id=self.user_id, display_name="Local user", provider=self.name)


_provider: AuthProvider | None = None


def _build_provider(name: str) -> AuthProvider:
    """Construct the configured provider.

    Imported lazily so the local path carries no dependency on the Google one,
    and an unknown name fails loudly rather than silently falling back to the
    unauthenticated provider.
    """
    key = (name or "").strip().lower()

    if key in ("", "local"):
        return LocalAuthProvider()

    if key == "google":
        from app.security.google_auth import GoogleAuthProvider

        return GoogleAuthProvider()

    raise ValueError(
        f"Unknown AUTH_PROVIDER {name!r}. Supported values are 'local' and 'google'."
    )


def get_auth_provider() -> AuthProvider:
    global _provider
    if _provider is None:
        _provider = _build_provider(settings.auth_provider)
    return _provider


def set_auth_provider(provider: AuthProvider | None) -> None:
    global _provider
    _provider = provider


async def get_current_user(request: Request) -> User:
    """FastAPI dependency returning the requesting user."""
    from fastapi import HTTPException, status

    user = await get_auth_provider().authenticate(request)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    return user
