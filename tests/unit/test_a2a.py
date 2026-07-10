"""Unit tests for shared.a2a (in-process ADK Runner A2A client).

We mock `shared.a2a._get_runner` (or `_build_runner` for the cache test) so
the test never constructs a real agent or calls Gemini. The contract under
test is:

1. `call_peer_agent` packs the `{mode, input}` envelope into a JSON text
   part and forwards `partner_id` as the Runner's `user_id`.
2. Stream events are consumed; the final event is returned as a JSON-safe
   dict (`exclude_none=True` dump).
3. Runner errors propagate as-is rather than being silently swallowed.
4. Runners are built once per peer and cached.
5. `call_concierge` aggregates narration + `route_to_*` tool pairing.

Event fixtures are REAL `google.adk.events.Event` objects so the typed
aggregation path (`event.content.parts` / `part.function_call` /
`part.function_response`) is exercised against the true wire types.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from google.adk.events import Event
from google.genai import types

from shared import a2a

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _ev(*parts: types.Part) -> Event:
    """Build a real ADK event with the given content parts."""
    return Event(author="peer", content=types.Content(role="model", parts=list(parts)))


class FakeRunner:
    """Stands in for a `google.adk.runners.Runner`.

    Records the kwargs of the last `run_async` call in `captured` and
    replays `events` (or raises `error` mid-stream).
    """

    def __init__(self, events: list[Event], error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.captured: dict[str, Any] = {}

    async def run_async(self, **kwargs: Any) -> AsyncIterator[Event]:
        self.captured.update(kwargs)
        if self.error is not None:
            raise self.error
        for ev in self.events:
            yield ev


def _patch_runner(monkeypatch: pytest.MonkeyPatch, fake: FakeRunner) -> None:
    async def _get(_peer: str) -> FakeRunner:
        return fake

    monkeypatch.setattr(a2a, "_get_runner", _get)


async def test_call_peer_agent_packs_envelope_and_returns_final_event(monkeypatch):
    fake = FakeRunner([
        _ev(types.Part(text="thinking...")),
        _ev(types.Part(text="final answer")),
    ])
    _patch_runner(monkeypatch, fake)

    out = await a2a.call_peer_agent(
        peer="pricing",
        mode="price_listing",
        input={"category": "prepared_food", "retail_value": 96},
        partner_id="sk_demo_surplus_2026",
    )

    # Envelope: a single JSON text part carrying {mode, input}.
    sent = fake.captured["new_message"]
    assert json.loads(sent.parts[0].text) == {
        "mode": "price_listing",
        "input": {"category": "prepared_food", "retail_value": 96},
    }
    assert fake.captured["user_id"] == "sk_demo_surplus_2026"
    # session_id=None at the façade becomes a real per-call session id.
    assert isinstance(fake.captured["session_id"], str)
    assert fake.captured["session_id"]

    # Final event round-trips as a JSON-safe dict (no None-valued keys).
    assert out["content"]["parts"][0]["text"] == "final answer"
    assert "function_call" not in out["content"]["parts"][0]


async def test_call_peer_agent_raises_on_unknown_peer():
    with pytest.raises(ValueError, match="unknown agent"):
        a2a.load_agent("not_a_peer")


async def test_call_peer_agent_propagates_runner_errors(monkeypatch):
    fake = FakeRunner([], error=PermissionError("not authorized"))
    _patch_runner(monkeypatch, fake)

    with pytest.raises(PermissionError, match="not authorized"):
        await a2a.call_peer_agent(
            peer="pricing",
            mode="price_listing",
            input={},
            partner_id="sk_demo",
        )


async def test_call_peer_agent_raises_on_empty_stream(monkeypatch):
    fake = FakeRunner([])
    _patch_runner(monkeypatch, fake)

    with pytest.raises(RuntimeError, match="zero events"):
        await a2a.call_peer_agent(
            peer="pricing",
            mode="price_listing",
            input={},
            partner_id="sk_demo",
        )


async def test_runner_is_cached_per_peer(monkeypatch):
    build_count = 0
    fake = FakeRunner([_ev(types.Part(text="ok"))])

    def _build(_peer: str) -> FakeRunner:
        nonlocal build_count
        build_count += 1
        return fake

    monkeypatch.setattr(a2a, "_build_runner", _build)

    await a2a.call_peer_agent("pricing", "price_listing", {}, "sk_demo")
    await a2a.call_peer_agent("pricing", "price_listing", {}, "sk_demo")

    assert build_count == 1, "runner should be built once per peer and cached"


# ---------------------------------------------------------------------------
# call_concierge — stream aggregator
# ---------------------------------------------------------------------------


async def test_call_concierge_extracts_narration_from_final_text(monkeypatch):
    """When Concierge replies with prose (no tool), the final part.text becomes narration."""
    fake = FakeRunner([
        _ev(types.Part(text="I can help you onboard, list, price, or resolve a dispute.")),
    ])
    _patch_runner(monkeypatch, fake)

    result = await a2a.call_concierge(
        user_message="What do you do?",
        partner_id="sk_demo",
    )
    assert result["narration"].startswith("I can help you onboard")
    assert result["specialist_called"] is None
    assert result["specialist_payload"] == {}
    assert result["event_count"] == 1


async def test_call_concierge_captures_specialist_from_tool_call(monkeypatch):
    """When the model invokes route_to_*, the suffix becomes specialist_called."""
    fake = FakeRunner([
        # event 1: model decides to call route_to_listing_intake
        _ev(types.Part(function_call=types.FunctionCall(
            name="route_to_listing_intake",
            args={"message": "10 sandwiches", "partner_id": "sk_demo"},
        ))),
        # event 2: tool response comes back
        _ev(types.Part(function_response=types.FunctionResponse(
            name="route_to_listing_intake",
            response={"status": "ok", "listing_id": "abc-123"},
        ))),
        # event 3: model narrates
        _ev(types.Part(text="Saved 10 sandwiches at $7.25.")),
    ])
    _patch_runner(monkeypatch, fake)

    result = await a2a.call_concierge(
        user_message="Save these 10 turkey sandwiches",
        partner_id="sk_demo",
    )
    assert result["specialist_called"] == "listing_intake"
    assert result["specialist_payload"] == {"status": "ok", "listing_id": "abc-123"}
    assert result["narration"] == "Saved 10 sandwiches at $7.25."
    assert result["event_count"] == 3


async def test_call_concierge_ignores_non_routing_tool_calls(monkeypatch):
    """A tool call that isn't `route_to_*` shouldn't populate specialist_called."""
    fake = FakeRunner([
        _ev(types.Part(function_call=types.FunctionCall(
            name="some_other_tool", args={},
        ))),
        _ev(types.Part(function_response=types.FunctionResponse(
            name="some_other_tool", response={"x": 1},
        ))),
        _ev(types.Part(text="Done.")),
    ])
    _patch_runner(monkeypatch, fake)

    result = await a2a.call_concierge(
        user_message="x", partner_id="sk_demo",
    )
    assert result["specialist_called"] is None
    assert result["specialist_payload"] == {}
    assert result["narration"] == "Done."
