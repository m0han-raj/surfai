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


class OAuthAuthProvider(AuthProvider):  # pragma: no cover - future work
    """Placeholder for a future OAuth provider.

    Deliberately not implemented: shipping a half-working auth path would be
    worse than shipping none. It exists to fix the shape of the interface.
    """

    name = "oauth"

    async def authenticate(self, request: Request) -> User | None:
        raise NotImplementedError(
            "OAuth authentication is not part of the MVP. See the roadmap in README.md."
        )


_PROVIDERS: dict[str, type[AuthProvider]] = {
    "local": LocalAuthProvider,
    "oauth": OAuthAuthProvider,
}

_provider: AuthProvider | None = None


def get_auth_provider() -> AuthProvider:
    global _provider
    if _provider is None:
        provider_cls = _PROVIDERS.get(settings.auth_provider.lower(), LocalAuthProvider)
        _provider = provider_cls()
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
