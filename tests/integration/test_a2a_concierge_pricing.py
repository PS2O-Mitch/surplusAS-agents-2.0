"""Integration test: Concierge → Pricing over the in-process A2A client.

The Pricing runner is mocked at the transport boundary
(`shared.a2a._get_runner`), so this runs in CI without a Gemini key. What
it verifies:

1. The Concierge-side path packs `mode + input` into the JSON text-part
   envelope `shared.a2a` builds, and forwards `partner_id` as `user_id`.
2. `session_id=None` at the façade becomes a real per-call session id.
3. The final stream event the Pricing agent produces round-trips through
   `call_peer_agent` intact: `applied_pressures`, `formula_version`,
   `recommended_price` all recoverable by the caller.

This is marked `integration` so it stays out of the unit-test fast path.
The live-model smoke is `python -m evals.runner --agent pricing --mode remote`.
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

pytestmark = pytest.mark.integration


def _ev(*parts: types.Part) -> Event:
    return Event(author="pricing", content=types.Content(role="model", parts=list(parts)))


class FakeRunner:
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


async def test_concierge_to_pricing_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concierge dispatches `price_listing` to Pricing; Pricing returns a recommendation."""
    pricing_payload = {
        "status": "ok",
        "recommendation": {
            "recommendation_id": "11111111-1111-1111-1111-111111111111",
            "recommended_price": 7.25,
            "recommended_discount_pct": 0.40,
            "anchor_p50": 11.50,
            "anchor_source": "apify",
            "anchor_region": "US-FL",
            "applied_pressures": {
                "base": 0.10,
                "expiry": 0.30,
                "inventory": 0.05,
                "time_of_day": 0.05,
                "merchant_floor": 0.10,
                "clamped_to_floor": False,
                "clamped_to_retail": False,
            },
            "formula_version": "v1",
            "coefficients_version": 7,
            "replay_of": None,
        },
        "narration": "Priced at $7.25 — anchor $11.50, expiry is the dominant pressure.",
    }

    fake = FakeRunner([
        _ev(types.Part(text="looking up anchor...")),
        _ev(types.Part(text="running formula...")),
        # The agent's final turn carries the structured payload as JSON text
        # (the shape evals/runner.py:_extract_payload peels back out).
        _ev(types.Part(text=json.dumps(pricing_payload))),
    ])
    _patch_runner(monkeypatch, fake)

    out = await a2a.call_peer_agent(
        peer="pricing",
        mode="price_listing",
        input={
            "category": "prepared_meal",
            "region": "US-FL-Hillsborough",
            "units": 1,
            "retail_value": 12.00,
            "hours_until_expiry": 4.0,
            "now_hour": 18,
            "merchant_floor_pct": 0.10,
        },
        partner_id="sk_demo_surplus_2026",
    )

    # Wire-shape assertions (what Concierge sends Pricing).
    assert json.loads(fake.captured["new_message"].parts[0].text) == {
        "mode": "price_listing",
        "input": {
            "category": "prepared_meal",
            "region": "US-FL-Hillsborough",
            "units": 1,
            "retail_value": 12.00,
            "hours_until_expiry": 4.0,
            "now_hour": 18,
            "merchant_floor_pct": 0.10,
        },
    }
    assert fake.captured["user_id"] == "sk_demo_surplus_2026"
    assert isinstance(fake.captured["session_id"], str)
    assert fake.captured["session_id"]

    # Final-event-shape assertions (what Concierge can rely on for narration).
    payload = json.loads(out["content"]["parts"][0]["text"])
    assert payload["status"] == "ok"
    rec = payload["recommendation"]
    assert rec["recommended_price"] == 7.25
    assert rec["formula_version"] == "v1"
    assert "applied_pressures" in rec
    assert set(rec["applied_pressures"].keys()) >= {
        "base",
        "expiry",
        "inventory",
        "time_of_day",
        "merchant_floor",
    }


async def test_pricing_no_anchor_propagates_to_concierge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the Pricing engine has no anchor, the agent surfaces it cleanly."""
    fake = FakeRunner([
        _ev(types.Part(text=json.dumps({
            "status": "no_anchor",
            "narration": "No reference price for category=prepared_meal region=ZZ — "
                         "listing parked as draft_no_price.",
        }))),
    ])
    _patch_runner(monkeypatch, fake)

    out = await a2a.call_peer_agent(
        peer="pricing",
        mode="price_listing",
        input={"category": "prepared_meal", "region": "ZZ", "units": 1,
               "retail_value": 10, "hours_until_expiry": 4, "now_hour": 12,
               "merchant_floor_pct": 0.10},
        partner_id="sk_demo",
    )

    payload = json.loads(out["content"]["parts"][0]["text"])
    assert payload["status"] == "no_anchor"
    assert "draft_no_price" in payload["narration"]


async def test_pricing_runner_error_surfaces_to_concierge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concierge must see the raised exception (so the gateway can map it)."""
    fake = FakeRunner([], error=PermissionError("pricing agent misconfigured"))
    _patch_runner(monkeypatch, fake)

    with pytest.raises(PermissionError, match="misconfigured"):
        await a2a.call_peer_agent(
            peer="pricing",
            mode="price_listing",
            input={"category": "prepared_meal", "region": "US-FL", "units": 1,
                   "retail_value": 10, "hours_until_expiry": 4, "now_hour": 12,
                   "merchant_floor_pct": 0.10},
            partner_id="sk_demo",
        )
