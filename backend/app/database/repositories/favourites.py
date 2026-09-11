"""Favourite persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models import Favourite


def _now() -> datetime:
    return datetime.now(UTC)


def to_dict(favourite: Favourite) -> dict[str, Any]:
    return {
        "id": favourite.id,
        "name": favourite.name,
        "url": favourite.url,
        "domain": favourite.domain,
        "intent": favourite.intent,
        "description": favourite.description,
        "preferences": favourite.preferences or {},
        "metadata": favourite.meta or {},
        "created_at": favourite.created_at.isoformat() if favourite.created_at else None,
        "updated_at": favourite.updated_at.isoformat() if favourite.updated_at else None,
    }


class FavouriteRepository:
    def __init__(self, session: Session, user_id: str = "local-user") -> None:
        self.session = session
        self.user_id = user_id

    def list(self, *, domain: str | None = None, limit: int = 200) -> list[Favourite]:
        stmt = select(Favourite).where(Favourite.user_id == self.user_id)
        if domain:
            stmt = stmt.where(Favourite.domain == domain.lower())
        stmt = stmt.order_by(Favourite.updated_at.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def get(self, favourite_id: str) -> Favourite | None:
        favourite = self.session.get(Favourite, favourite_id)
        if favourite is None or favourite.user_id != self.user_id:
            return None
        return favourite

    def create(self, data: dict[str, Any]) -> Favourite:
        favourite = Favourite(
            user_id=self.user_id,
            name=(data.get("name") or "Untitled").strip()[:200],
            url=(data.get("url") or "").strip(),
            domain=(data.get("domain") or _domain_of(data.get("url", ""))).lower()[:255],
            intent=(data.get("intent") or "").strip(),
            description=(data.get("description") or "").strip(),
            preferences=data.get("preferences") or {},
            meta=data.get("metadata") or {},
        )
        self.session.add(favourite)
        self.session.flush()
        return favourite

    def update(self, favourite_id: str, data: dict[str, Any]) -> Favourite | None:
        favourite = self.get(favourite_id)
        if favourite is None:
            return None
        for field in ("name", "url", "intent", "description"):
            if data.get(field) is not None:
                setattr(favourite, field, str(data[field]).strip())
        if data.get("domain") is not None:
            favourite.domain = str(data["domain"]).lower()[:255]
        elif data.get("url") is not None:
            favourite.domain = _domain_of(str(data["url"]))
        if data.get("preferences") is not None:
            favourite.preferences = data["preferences"]
        if data.get("metadata") is not None:
            favourite.meta = data["metadata"]
        favourite.updated_at = _now()
        self.session.flush()
        return favourite

    def delete(self, favourite_id: str) -> bool:
        favourite = self.get(favourite_id)
        if favourite is None:
            return False
        self.session.delete(favourite)
        self.session.flush()
        return True

    def list_as_dicts(self) -> list[dict[str, Any]]:
        return [to_dict(f) for f in self.list()]


def _domain_of(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return urlparse(url).netloc.lower()
    except ValueError:
        return ""
