"""Reading and removing stored chat history.

Writing happens in `chat.py`, as a side effect of answering, which is why the
panel closing mid-answer does not lose the turn. There is deliberately no
endpoint for appending: a conversation is a record of what was said, and
letting a client write arbitrary turns into one would make it a record of what
a client claimed was said.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from app.database.repositories.conversations import ConversationRepository
from app.security.permissions import User, get_current_user

router = APIRouter(prefix="/api", tags=["conversations"])


@router.get("/conversations")
async def list_conversations(limit: int = 50, user: User = Depends(get_current_user)) -> dict:
    """Recent conversations without their transcripts.

    Opening the History tab should not fetch every message the user has ever
    exchanged, so the messages are a separate request for the one they pick.
    """
    conversations = ConversationRepository(user.id).list(limit=max(1, min(limit, 200)))
    return {"conversations": conversations, "total": len(conversations)}


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str, user: User = Depends(get_current_user)
) -> dict:
    conversation = ConversationRepository(user.id).get(conversation_id)
    if conversation is None:
        # The same answer for "no such conversation" and "not yours", so the
        # endpoint cannot be used to discover that an id exists.
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str, user: User = Depends(get_current_user)
) -> Response:
    if not ConversationRepository(user.id).delete(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    # Explicit: a 204 must carry no body, and returning None here would have
    # FastAPI build a response model around NoneType and fail.
    return Response(status_code=204)
