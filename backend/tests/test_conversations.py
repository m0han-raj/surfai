"""Stored chat history.

Two things matter more than the storage working. One person's conversations
must never be visible to another, which is the same rule favourites and tasks
already follow. And recording history must never be able to break a chat turn:
this is a convenience, and a convenience that can swallow the answer you were
waiting for is not one.
"""

from __future__ import annotations

import pytest

from app.database.repositories.conversations import ConversationRepository


@pytest.fixture
def repo(database) -> ConversationRepository:
    return ConversationRepository(user_id="user-a")


@pytest.fixture
def other(database) -> ConversationRepository:
    return ConversationRepository(user_id="user-b")


# --- recording ------------------------------------------------------------


def test_a_turn_starts_a_conversation_when_there_is_none(repo) -> None:
    conversation_id = repo.record(
        conversation_id=None,
        user_message="what is this page about?",
        reply="A recipe for carbonara.",
        page_url="https://cooking.example.com/carbonara",
    )

    assert conversation_id
    stored = repo.get(conversation_id)
    assert [m["role"] for m in stored["messages"]] == ["user", "assistant"]
    assert stored["messages"][0]["content"] == "what is this page about?"
    assert stored["messages"][1]["content"] == "A recipe for carbonara."


def test_later_turns_join_the_same_conversation(repo) -> None:
    first = repo.record(conversation_id=None, user_message="hello", reply="Hi.")
    second = repo.record(conversation_id=first, user_message="and again", reply="Sure.")

    assert second == first
    assert len(repo.get(first)["messages"]) == 4


def test_the_title_comes_from_the_first_thing_the_user_said(repo) -> None:
    """A model call per conversation would be a real cost for a list heading."""
    conversation_id = repo.record(
        conversation_id=None,
        user_message="how do I export my bookmarks?",
        reply="Open the bookmark manager.",
    )
    assert repo.get(conversation_id)["title"] == "how do I export my bookmarks?"


def test_a_long_first_message_is_truncated_to_fit_the_column(repo) -> None:
    conversation_id = repo.record(
        conversation_id=None, user_message="x" * 500, reply="ok"
    )
    assert len(repo.get(conversation_id)["title"]) <= 200


def test_the_title_does_not_change_on_later_turns(repo) -> None:
    conversation_id = repo.record(conversation_id=None, user_message="first", reply="a")
    repo.record(conversation_id=conversation_id, user_message="second", reply="b")

    assert repo.get(conversation_id)["title"] == "first"


def test_the_page_a_turn_was_about_is_kept(repo) -> None:
    """So reopening a conversation shows where its subject changed."""
    conversation_id = repo.record(
        conversation_id=None,
        user_message="what is this?",
        reply="A recipe.",
        page_url="https://cooking.example.com/carbonara",
    )
    assert repo.get(conversation_id)["messages"][0]["page_url"] == (
        "https://cooking.example.com/carbonara"
    )


def test_warnings_travel_with_the_reply_they_belong_to(repo) -> None:
    conversation_id = repo.record(
        conversation_id=None,
        user_message="what does this say?",
        reply="It is a checkout page.",
        warnings=["This page tried to give the assistant instructions."],
    )
    messages = repo.get(conversation_id)["messages"]

    assert messages[1]["warnings"] == ["This page tried to give the assistant instructions."]
    assert messages[0]["warnings"] == []


# --- who can see what -----------------------------------------------------


def test_a_conversation_belongs_to_one_person(repo, other) -> None:
    mine = repo.record(conversation_id=None, user_message="private", reply="ok")

    assert other.get(mine) is None
    assert other.list() == []


def test_writing_into_someone_else_s_conversation_is_refused(repo, other) -> None:
    """Otherwise a guessed id would let one account append to another's thread."""
    mine = repo.record(conversation_id=None, user_message="private", reply="ok")

    # Their write starts a conversation of their own rather than joining mine.
    theirs = other.record(conversation_id=mine, user_message="intruding", reply="no")

    assert theirs != mine
    assert len(repo.get(mine)["messages"]) == 2


def test_deleting_someone_else_s_conversation_is_refused(repo, other) -> None:
    mine = repo.record(conversation_id=None, user_message="private", reply="ok")

    assert other.delete(mine) is False
    assert repo.get(mine) is not None


# --- listing and removing -------------------------------------------------


def test_the_most_recently_used_conversation_comes_first(repo) -> None:
    older = repo.record(conversation_id=None, user_message="older", reply="a")
    newer = repo.record(conversation_id=None, user_message="newer", reply="b")
    # Touching the older one should float it back to the top.
    repo.record(conversation_id=older, user_message="again", reply="c")

    assert [c["id"] for c in repo.list()][:2] == [older, newer]


def test_the_listing_carries_enough_to_choose_from_without_the_whole_thread(repo) -> None:
    repo.record(conversation_id=None, user_message="what is this page about?", reply="A recipe.")
    [listed] = repo.list()

    assert listed["title"] == "what is this page about?"
    assert listed["message_count"] == 2
    assert listed["updated_at"]
    # The transcript is fetched separately; a list of ten threads should not
    # drag every message either of us ever sent along with it.
    assert "messages" not in listed


def test_deleting_a_conversation_takes_its_messages_with_it(repo) -> None:
    conversation_id = repo.record(conversation_id=None, user_message="hello", reply="hi")

    assert repo.delete(conversation_id) is True
    assert repo.get(conversation_id) is None
    assert repo.list() == []


def test_deleting_something_that_is_not_there_is_not_an_error(repo) -> None:
    assert repo.delete("no-such-conversation") is False


def test_an_unknown_conversation_reads_as_absent(repo) -> None:
    assert repo.get("no-such-conversation") is None


# --- notices --------------------------------------------------------------


def test_a_page_change_is_recorded_as_part_of_the_conversation(repo) -> None:
    """Reopening a thread should show where the subject changed, not just a
    run of messages that quietly start being about something else."""
    conversation_id = repo.record(
        conversation_id=None, user_message="what is this?", reply="A recipe."
    )
    repo.note(
        conversation_id,
        "Switched to flights.example.com. I'll answer about this page from now on.",
        page_url="https://flights.example.com/search",
    )

    roles = [m["role"] for m in repo.get(conversation_id)["messages"]]
    assert roles == ["user", "assistant", "notice"]


def test_a_notice_for_someone_else_s_conversation_is_refused(repo, other) -> None:
    mine = repo.record(conversation_id=None, user_message="private", reply="ok")
    other.note(mine, "Switched to evil.example.com.", page_url="https://evil.example.com")

    assert len(repo.get(mine)["messages"]) == 2
