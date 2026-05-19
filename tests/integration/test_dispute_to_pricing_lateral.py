"""Integration test: Dispute Triage -> Pricing over A2A SDK (lateral edge).

The Pricing handle is mocked at the SDK boundary (`vertexai.agent_engines.get`),
so this runs in CI without GCP credentials. What it verifies:

1. `request_reprice` sends a **plain-string** request to Pricing (the
   shape ADK Runner expects) — not the legacy `{mode, input}` envelope.
2. The request names the `replay_recommendation` tool, the original
   recommendation_id, and the partner_id.
3. The aggregator extracts the structured `replay_recommendation` tool
   response (new_recommendation_id, recommended_price, applied_pressures)
   from the stream's `function_response` part.
4. `request_reprice` returns `{status: ok, new_recommendation_id, new_price,
   new_pressures, narration}` for the Dispute Triage model to use directly.

Lateral edge per CLAUDE.md ("Dispute Triage -> Pricing") — pinned here so a
regression in the wire shape fails CI even before a remote eval is run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from agents.dispute_triage.tools import request_reprice
from shared import a2a
from shared.config import get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration


def _make_async_iter(events: list[dict[str, Any]]):
    async def _gen() -> AsyncIterator[dict[str, Any]]:
        for ev in events:
            yield ev
    return _gen()


@pytest.fixture(autouse=True)
def _seed_pricing_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv(
        "PRICING_AGENT_RESOURCE",
        "projects/1/locations/us-central1/reasoningEngines/pricing-id",
    )
    yield
    get_settings.cache_clear()


async def test_dispute_to_pricing_wire_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pricing must receive a plain-string `replay_recommendation` request."""
    captured: dict[str, Any] = {}

    pricing_events = [
        {"content": {"parts": [{"function_call": {
            "name": "replay_recommendation",
            "args": {"recommendation_id":
                     "11111111-1111-1111-1111-111111111111",
                     "partner_id": "sk_demo_surplus_2026"},
        }}], "role": "model"}, "author": "pricing"},
        {"content": {"parts": [{"function_response": {
            "name": "replay_recommendation",
            "response": {
                "status": "ok",
                "recommendation": {
                    "recommendation_id":
                        "55555555-5555-5555-5555-555555555555",
                    "recommended_price": 6.50,
                    "applied_pressures": {
                        "base": 0.10, "expiry": 0.21, "inventory": 0.05,
                        "time_of_day": 0.05, "merchant_floor": 0.10,
                        "clamped_to_floor": False, "clamped_to_retail": False,
                    },
                    "formula_version": "v1",
                    "replay_of": "11111111-1111-1111-1111-111111111111",
                },
            },
        }}], "role": "user"}, "author": "pricing"},
        {"content": {"parts": [{"text":
            "Replayed under fresh coefficients: new price $6.50, "
            "expiry pressure now 0.21."}],
            "role": "model"}, "author": "pricing"},
    ]

    pricing_handle = MagicMock()

    def _async_stream_query(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _make_async_iter(pricing_events)

    pricing_handle.async_stream_query = _async_stream_query
    monkeypatch.setattr(a2a.agent_engines, "get", lambda _r: pricing_handle)

    out = await request_reprice(
        original_recommendation_id="11111111-1111-1111-1111-111111111111",
        partner_id="sk_demo_surplus_2026",
    )

    # Pricing receives a STRING (not a dict envelope).
    msg = captured["message"]
    assert isinstance(msg, str)
    assert "replay_recommendation" in msg
    assert "11111111-1111-1111-1111-111111111111" in msg
    assert "sk_demo_surplus_2026" in msg
    assert captured["user_id"] == "sk_demo_surplus_2026"

    # Structured fields extracted from the function_response part:
    assert out["status"] == "ok"
    assert out["new_recommendation_id"] == "55555555-5555-5555-5555-555555555555"
    assert out["new_price"] == 6.50
    assert out["new_pressures"]["expiry"] == 0.21
    assert out["new_pressures"]["clamped_to_floor"] is False
    assert "6.50" in out["narration"]


async def test_dispute_reprice_error_when_pricing_skips_replay_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Pricing narrates without invoking `replay_recommendation`, we surface
    an error rather than fabricating a recommendation_id."""
    pricing_handle = MagicMock()
    pricing_handle.async_stream_query = lambda **_: _make_async_iter([
        {"content": {"parts": [{"text":
            "I couldn't find a prior recommendation matching that id."}],
            "role": "model"}, "author": "pricing"},
    ])
    monkeypatch.setattr(a2a.agent_engines, "get", lambda _r: pricing_handle)

    out = await request_reprice(
        original_recommendation_id="11111111-1111-1111-1111-111111111111",
        partner_id="sk_demo",
    )
    assert out["status"] == "error"
    assert "replay_recommendation" in out["error"]
