"""Favourites CRUD and natural-language resolution."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.agents.memory_agent import MemoryAgent
from app.api.schemas import FavouriteCreate, FavouriteResponse, FavouriteUpdate
from app.database.database import get_db
from app.database.repositories.favourites import FavouriteRepository, to_dict
from app.llm.openai_compatible import get_provider
from app.security.permissions import User, get_current_user

router = APIRouter(prefix="/api/favourites", tags=["favourites"])


def _repo(session: Session, user: User) -> FavouriteRepository:
    return FavouriteRepository(session, user.id)


@router.get("", response_model=list[FavouriteResponse])
async def list_favourites(
    domain: str | None = None,
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[dict]:
    return [to_dict(f) for f in _repo(session, user).list(domain=domain)]


@router.post("", response_model=FavouriteResponse, status_code=status.HTTP_201_CREATED)
async def create_favourite(
    payload: FavouriteCreate,
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    favourite = _repo(session, user).create(payload.model_dump())
    return to_dict(favourite)


@router.post("/resolve")
async def resolve_favourite(
    query: str = Body(embed=True, min_length=1, max_length=500),
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Resolve a phrase like "my AI jobs" to a saved favourite.

    Lexical matching runs first and answers most queries without a model call;
    the LLM is consulted only to break a tie.
    """
    favourites = _repo(session, user).list_as_dicts()
    match = await MemoryAgent(get_provider()).resolve(query, favourites)
    return {
        "found": match.found,
        "favourite": match.favourite,
        "score": round(match.score, 3),
        "method": match.method,
        "alternatives": match.alternatives,
    }


@router.get("/{favourite_id}", response_model=FavouriteResponse)
async def get_favourite(
    favourite_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    favourite = _repo(session, user).get(favourite_id)
    if favourite is None:
        raise HTTPException(status_code=404, detail="Favourite not found")
    return to_dict(favourite)


@router.put("/{favourite_id}", response_model=FavouriteResponse)
async def update_favourite(
    favourite_id: str,
    payload: FavouriteUpdate,
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    favourite = _repo(session, user).update(
        favourite_id, payload.model_dump(exclude_unset=True)
    )
    if favourite is None:
        raise HTTPException(status_code=404, detail="Favourite not found")
    return to_dict(favourite)


@router.delete("/{favourite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_favourite(
    favourite_id: str,
    session: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    if not _repo(session, user).delete(favourite_id):
        raise HTTPException(status_code=404, detail="Favourite not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
