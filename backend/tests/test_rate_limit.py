"""Waiting out a rate limit instead of failing the task.

Every free tier prices tokens per minute, and a multi-step agent task costs
more than a minute's worth: one planner call on a real page is ~2,800 tokens
after trimming, and a five-step task is about 14,000 against limits of 8,000 or
12,000. The budget is a rate, not a quota, so it refills. Failing at the moment
it runs out throws away a task that would have finished a few seconds later.

The provider now reads how long to wait, waits, and carries on. What these
tests pin is that it waits the right amount, gives up eventually, and never
silently turns a real error into a delay.
"""

from __future__ import annotations

import pytest

from app.llm.openai_compatible import OpenAICompatibleProvider, retry_after_seconds
from app.llm.provider import LLMResponseError, Message


class FakeResponse:
    def __init__(self, status_code: int, text: str = "", headers: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self):
        import json as _json

        return _json.loads(self.text)


GROQ_429 = (
    '{"error":{"message":"Rate limit reached for model `llama-3.3-70b-versatile` in '
    "organization `org_x` service tier `on_demand` on tokens per minute (TPM): Limit "
    '12000, Used 7530, Requested 5515. Please try again in 5.225s.","type":"tokens",'
    '"code":"rate_limit_exceeded"}}'
)

OK_BODY = '{"choices":[{"message":{"content":"done"},"finish_reason":"stop"}],"model":"m"}'


# --- reading the delay ----------------------------------------------------


def test_it_reads_the_delay_out_of_the_message() -> None:
    """Groq puts it in prose rather than only in a header."""
    assert retry_after_seconds(GROQ_429, {}) == pytest.approx(5.225, abs=0.01)


def test_it_prefers_the_retry_after_header_when_there_is_one() -> None:
    assert retry_after_seconds("", {"retry-after": "12"}) == 12.0


def test_it_reads_a_delay_given_in_minutes() -> None:
    """Groq switches to "1m30s" once the wait passes a minute.

    Any such delay is longer than the cap by definition, so what is asserted
    here is that the minutes are parsed rather than ignored -- reading only the
    seconds would turn 1m30s into a 30-second wait and a second rate limit.
    """
    from app.llm.openai_compatible import _RETRY_IN

    minutes, seconds = _RETRY_IN.search("Please try again in 1m30s.").groups()
    assert (float(minutes), float(seconds)) == (1.0, 30.0)

    # And the wait itself is capped, because the user is sitting through it.
    assert retry_after_seconds('{"error":{"message":"try again in 1m30s."}}', {}) == 60.0


def test_an_unreadable_body_still_yields_a_sane_wait() -> None:
    """A delay we cannot parse is not a reason to hammer the endpoint."""
    delay = retry_after_seconds("<html>429</html>", {})
    assert 1 <= delay <= 60


def test_an_absurd_delay_is_capped() -> None:
    """A minute is worth waiting; an hour is not, and the user is watching."""
    body = '{"error":{"message":"Please try again in 3600s."}}'
    assert retry_after_seconds(body, {}) <= 60


# --- waiting and carrying on ----------------------------------------------


@pytest.mark.asyncio
async def test_it_waits_then_succeeds(monkeypatch) -> None:
    provider = OpenAICompatibleProvider(base_url="https://x/v1", model="m")
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    responses = [FakeResponse(429, GROQ_429), FakeResponse(200, OK_BODY)]

    class FakeClient:
        async def post(self, *args, **kwargs):
            return responses.pop(0)

    monkeypatch.setattr(provider, "_get_client", lambda: _async(FakeClient()))
    monkeypatch.setattr("app.llm.openai_compatible.asyncio.sleep", fake_sleep)

    reply = await provider.generate([Message(role="user", content="hi")])

    assert reply.content == "done"
    assert slept == [pytest.approx(5.225, abs=0.01)], "it did not wait the time it was told"


@pytest.mark.asyncio
async def test_it_gives_up_rather_than_waiting_forever(monkeypatch) -> None:
    provider = OpenAICompatibleProvider(base_url="https://x/v1", model="m")

    async def fake_sleep(seconds):
        return None

    class AlwaysLimited:
        async def post(self, *args, **kwargs):
            return FakeResponse(429, GROQ_429)

    monkeypatch.setattr(provider, "_get_client", lambda: _async(AlwaysLimited()))
    monkeypatch.setattr("app.llm.openai_compatible.asyncio.sleep", fake_sleep)

    with pytest.raises(LLMResponseError) as caught:
        await provider.generate([Message(role="user", content="hi")])

    assert "429" in str(caught.value) or "rate limit" in str(caught.value).lower()


@pytest.mark.asyncio
async def test_other_errors_are_not_retried(monkeypatch) -> None:
    """A bad request will be just as bad in five seconds."""
    provider = OpenAICompatibleProvider(base_url="https://x/v1", model="m")
    calls: list[int] = []

    class BadRequest:
        async def post(self, *args, **kwargs):
            calls.append(1)
            return FakeResponse(400, '{"error":{"message":"bad schema"}}')

    monkeypatch.setattr(provider, "_get_client", lambda: _async(BadRequest()))

    with pytest.raises(LLMResponseError):
        await provider.generate([Message(role="user", content="hi")])

    assert len(calls) == 1, "a 400 was retried"


@pytest.mark.asyncio
async def test_bad_credentials_are_not_retried(monkeypatch) -> None:
    provider = OpenAICompatibleProvider(base_url="https://x/v1", model="m")
    calls: list[int] = []

    class Unauthorised:
        async def post(self, *args, **kwargs):
            calls.append(1)
            return FakeResponse(401, "nope")

    monkeypatch.setattr(provider, "_get_client", lambda: _async(Unauthorised()))

    with pytest.raises(LLMResponseError):
        await provider.generate([Message(role="user", content="hi")])

    assert len(calls) == 1


def _async(value):
    """Wrap a value in an awaitable, since `_get_client` is a coroutine."""

    async def coro():
        return value

    return coro()
