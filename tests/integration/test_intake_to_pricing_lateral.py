"""Integration test: Listing Intake -> Pricing over A2A SDK (lateral edge).

The Pricing handle is mocked at the SDK boundary (`vertexai.agent_engines.get`),
so this runs in CI without GCP credentials. What it verifies:

1. `request_anchor_price` packs the draft + region/floor/now_hour into the
   exact pricing_input shape Pricing expects (7 named keys with the right
   types).
2. The SDK's `async_stream_query` is called with `user_id == partner_id` and
   the correct A2A envelope (`{mode: "price_listing", input: {...}}`).
3. The final stream event is returned verbatim — Listing Intake doesn't
   transform the recommendation; it forwards Pricing's payload to its own
   prompt which then narrates + persists.

Lateral edge per CLAUDE.md ("Listing Intake -> Pricing") — pinned here so a
regression in the wire shape fails CI even before a remote eval is run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from agents.listing_intake.tools import request_anchor_price
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
    # Each test starts with an empty A2A handle cache.
    a2a._handle_cache.clear()
    yield
    get_settings.cache_clear()
    a2a._handle_cache.clear()


async def test_intake_to_pricing_wire_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pricing must receive the 7-key pricing_input + partner_id as user_id."""
    captured: dict[str, Any] = {}

    pricing_final_event = {
        "status": "ok",
        "recommendation": {
            "recommendation_id": "11111111-1111-1111-1111-111111111111",
            "recommended_price": 7.25,
            "applied_pressures": {
                "base": 0.10, "expiry": 0.30, "inventory": 0.05,
                "time_of_day": 0.05, "merchant_floor": 0.10,
                "clamped_to_floor": False, "clamped_to_retail": False,
            },
            "formula_version": "v1",
        },
    }

    pricing_handle = MagicMock()

    def _async_stream_query(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _make_async_iter([pricing_final_event])

    pricing_handle.async_stream_query = _async_stream_query
    monkeypatch.setattr(a2a.agent_engines, "get", lambda _resource: pricing_handle)

    draft = {
        "title": "Day-old turkey sandwiches",
        "description": "deli-made",
        "category": "prepared_meal",
        "units": 10,
        "retail_value": "12.00",
        "hours_until_expiry": "4",
        "image_uri": None,
    }

    out = await request_anchor_price(
        draft=draft,
        partner_id="sk_demo_surplus_2026",
        region="US-FL-Hillsborough",
        merchant_floor_pct=0.10,
        now_hour=18,
    )

    # Envelope shape: Pricing sees a structured mode/input dict.
    assert captured["message"] == {
        "mode": "price_listing",
        "input": {
            "category": "prepared_meal",
            "region": "US-FL-Hillsborough",
            "units": 10,
            "retail_value": 12.0,
            "hours_until_expiry": 4.0,
            "now_hour": 18,
            "merchant_floor_pct": 0.10,
        },
    }
    assert captured["user_id"] == "sk_demo_surplus_2026"

    # Listing Intake forwards Pricing's response verbatim.
    assert out["status"] == "ok"
    rec = out["recommendation"]
    assert rec["recommended_price"] == 7.25
    assert rec["formula_version"] == "v1"
    assert set(rec["applied_pressures"].keys()) >= {
        "base", "expiry", "inventory", "time_of_day", "merchant_floor",
    }


async def test_intake_no_anchor_surfaces_to_intake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Pricing returns no_anchor, Listing Intake gets it back cleanly."""
    pricing_handle = MagicMock()
    pricing_handle.async_stream_query = lambda **_: _make_async_iter([
        {"status": "no_anchor",
         "narration": "No reference price for category=prepared_meal region=ZZ"}
    ])
    monkeypatch.setattr(a2a.agent_engines, "get", lambda _r: pricing_handle)

    out = await request_anchor_price(
        draft={"title": "x", "category": "prepared_meal", "units": 1,
               "retail_value": "10", "hours_until_expiry": "4"},
        partner_id="sk_demo",
        region="ZZ",
        merchant_floor_pct=0.10,
        now_hour=12,
    )
    assert out["status"] == "no_anchor"
    assert "No reference price" in out["narration"]
