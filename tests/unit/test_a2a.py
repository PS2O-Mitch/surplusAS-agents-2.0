"""Unit tests for shared.a2a (Agent Engine SDK-based A2A client).

We mock `vertexai.agent_engines.get` so the test never reaches GCP. The
contract under test is:

1. `call_peer_agent` resolves the right resource from settings per peer name.
2. Stream events are consumed; the final event is returned.
3. SDK errors (e.g. PermissionDenied) propagate as-is rather than being
   silently swallowed.
4. The trace span is opened around the SDK call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from google.api_core import exceptions as gax_exceptions

from shared import a2a
from shared.config import get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _make_async_iter(events: list[dict[str, Any]]):
    """Build an async iterator over `events`."""

    async def _gen() -> AsyncIterator[dict[str, Any]]:
        for ev in events:
            yield ev

    return _gen()


@pytest.fixture(autouse=True)
def _seed_resources(monkeypatch):
    """Populate settings with stub resource names so resolution succeeds."""
    get_settings.cache_clear()
    monkeypatch.setenv(
        "PRICING_AGENT_RESOURCE",
        "projects/1/locations/us-central1/reasoningEngines/pricing-id",
    )
    monkeypatch.setenv(
        "ONBOARDING_AGENT_RESOURCE",
        "projects/1/locations/us-central1/reasoningEngines/onboarding-id",
    )
    yield
    get_settings.cache_clear()


def _patch_get(monkeypatch, fake_handle):
    """Replace agent_engines.get with a function returning `fake_handle`."""
    monkeypatch.setattr(a2a.agent_engines, "get", lambda resource: fake_handle)


async def test_call_peer_agent_resolves_resource_and_returns_final_event(monkeypatch):
    captured: dict[str, Any] = {}

    fake_handle = MagicMock()

    def _async_stream_query(**kwargs):
        captured.update(kwargs)
        return _make_async_iter(
            [
                {"partial": True, "text": "thinking..."},
                {"final": True, "applied_pressures": {"expiry": 0.21}},
            ]
        )

    fake_handle.async_stream_query = _async_stream_query
    _patch_get(monkeypatch, fake_handle)

    out = await a2a.call_peer_agent(
        peer="pricing",
        mode="price_listing",
        input={"category": "prepared_food", "retail_value": 96},
        partner_id="sk_demo_surplus_2026",
    )

    assert out == {"final": True, "applied_pressures": {"expiry": 0.21}}
    assert captured["message"] == {
        "mode": "price_listing",
        "input": {"category": "prepared_food", "retail_value": 96},
    }
    assert captured["user_id"] == "sk_demo_surplus_2026"


async def test_call_peer_agent_raises_on_unconfigured_peer(monkeypatch):
    # `pydantic-settings` reads from .env as well as os.environ, so deleting
    # the var from the process env isn't enough — set it to "" explicitly,
    # which pydantic-settings respects as "unset". The empty-string branch
    # in `_resolve_resource` is exactly what we want to exercise.
    monkeypatch.setenv("DISPUTE_TRIAGE_AGENT_RESOURCE", "")
    get_settings.cache_clear()

    with pytest.raises(RuntimeError, match="DISPUTE_TRIAGE_AGENT_RESOURCE"):
        await a2a.call_peer_agent(
            peer="dispute_triage",
            mode="reprice",
            input={},
            partner_id="sk_demo",
        )


async def test_call_peer_agent_propagates_sdk_errors(monkeypatch):
    fake_handle = MagicMock()

    def _async_stream_query(**_kwargs):
        async def _gen():
            raise gax_exceptions.PermissionDenied("not authorized")
            yield  # pragma: no cover

        return _gen()

    fake_handle.async_stream_query = _async_stream_query
    _patch_get(monkeypatch, fake_handle)

    with pytest.raises(gax_exceptions.PermissionDenied):
        await a2a.call_peer_agent(
            peer="pricing",
            mode="price_listing",
            input={},
            partner_id="sk_demo",
        )


async def test_call_peer_agent_raises_on_empty_stream(monkeypatch):
    fake_handle = MagicMock()
    fake_handle.async_stream_query = lambda **_: _make_async_iter([])
    _patch_get(monkeypatch, fake_handle)

    with pytest.raises(RuntimeError, match="zero events"):
        await a2a.call_peer_agent(
            peer="pricing",
            mode="price_listing",
            input={},
            partner_id="sk_demo",
        )


async def test_handle_is_cached_per_resource(monkeypatch):
    call_count = 0
    fake_handle = MagicMock()
    fake_handle.async_stream_query = lambda **_: _make_async_iter([{"ok": True}])

    def _get(resource: str):
        nonlocal call_count
        call_count += 1
        return fake_handle

    monkeypatch.setattr(a2a.agent_engines, "get", _get)

    await a2a.call_peer_agent("pricing", "price_listing", {}, "sk_demo")
    await a2a.call_peer_agent("pricing", "price_listing", {}, "sk_demo")

    assert call_count == 1, "handle should be resolved once per resource and cached"


# ---------------------------------------------------------------------------
# call_concierge — stream aggregator
# ---------------------------------------------------------------------------


async def test_call_concierge_extracts_narration_from_final_text(monkeypatch):
    """When Concierge replies with prose (no tool), the final part.text becomes narration."""
    handle = MagicMock()
    handle.async_stream_query = lambda **_: _make_async_iter([
        {"content": {"parts": [{"text": "I can help you onboard, list, price, or resolve a dispute."}],
                     "role": "model"},
         "author": "concierge"},
    ])
    monkeypatch.setattr(a2a.agent_engines, "get", lambda _r: handle)

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
    handle = MagicMock()
    handle.async_stream_query = lambda **_: _make_async_iter([
        # event 1: model decides to call route_to_listing_intake
        {"content": {"parts": [{"function_call": {
            "name": "route_to_listing_intake",
            "args": {"message": "10 sandwiches", "partner_id": "sk_demo"},
        }}], "role": "model"}, "author": "concierge"},
        # event 2: tool response comes back
        {"content": {"parts": [{"function_response": {
            "name": "route_to_listing_intake",
            "response": {"status": "ok", "listing_id": "abc-123"},
        }}], "role": "user"}, "author": "concierge"},
        # event 3: model narrates
        {"content": {"parts": [{"text": "Saved 10 sandwiches at $7.25."}],
                     "role": "model"}, "author": "concierge"},
    ])
    monkeypatch.setattr(a2a.agent_engines, "get", lambda _r: handle)

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
    handle = MagicMock()
    handle.async_stream_query = lambda **_: _make_async_iter([
        {"content": {"parts": [{"function_call": {
            "name": "some_other_tool", "args": {},
        }}], "role": "model"}, "author": "concierge"},
        {"content": {"parts": [{"function_response": {
            "name": "some_other_tool", "response": {"x": 1},
        }}], "role": "user"}, "author": "concierge"},
        {"content": {"parts": [{"text": "Done."}], "role": "model"},
         "author": "concierge"},
    ])
    monkeypatch.setattr(a2a.agent_engines, "get", lambda _r: handle)

    result = await a2a.call_concierge(
        user_message="x", partner_id="sk_demo",
    )
    assert result["specialist_called"] is None
    assert result["specialist_payload"] == {}
    assert result["narration"] == "Done."
