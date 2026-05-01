"""Integration test: Concierge → Pricing over A2A SDK.

Both agents are mocked at the SDK boundary (`vertexai.agent_engines.get`),
so this runs in CI without GCP credentials. What it verifies:

1. The Concierge-side path packs `mode + input + partner_id` into the
   message envelope `shared.a2a` builds.
2. The Pricing-side stream-query receives the right `user_id`
   (== partner_id) and message dict.
3. The final stream event the deployed Pricing agent will produce shape-
   matches what the Concierge's narration code expects: `applied_pressures`,
   `formula_version`, `recommended_price` all present.

This is marked `integration` so it stays out of the unit-test fast path.
The real cross-deployment smoke check happens in `scripts/smoke_agents.sh`
once both agents are deployed (Phase 2 step 9).
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


async def test_concierge_to_pricing_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Concierge dispatches `price_listing` to Pricing; Pricing returns a recommendation."""
    captured: dict[str, Any] = {}

    pricing_handle = MagicMock()
    pricing_final_event = {
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

    def _async_stream_query(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _make_async_iter(
            [
                {"partial": True, "text": "looking up anchor..."},
                {"partial": True, "text": "running formula..."},
                pricing_final_event,
            ]
        )

    pricing_handle.async_stream_query = _async_stream_query
    monkeypatch.setattr(a2a.agent_engines, "get", lambda resource: pricing_handle)

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
    assert captured["message"] == {
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
    assert captured["user_id"] == "sk_demo_surplus_2026"
    assert captured["session_id"] is None

    # Final-event-shape assertions (what Concierge can rely on for narration).
    assert out["status"] == "ok"
    rec = out["recommendation"]
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
    pricing_handle = MagicMock()
    pricing_handle.async_stream_query = lambda **_: _make_async_iter(
        [
            {
                "status": "no_anchor",
                "narration": "No reference price for category=prepared_meal region=ZZ — "
                             "listing parked as draft_no_price.",
            }
        ]
    )
    monkeypatch.setattr(a2a.agent_engines, "get", lambda resource: pricing_handle)

    out = await a2a.call_peer_agent(
        peer="pricing",
        mode="price_listing",
        input={"category": "prepared_meal", "region": "ZZ", "units": 1,
               "retail_value": 10, "hours_until_expiry": 4, "now_hour": 12,
               "merchant_floor_pct": 0.10},
        partner_id="sk_demo",
    )

    assert out["status"] == "no_anchor"
    assert "draft_no_price" in out["narration"]


async def test_pricing_permission_denied_surfaces_to_concierge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concierge must see a typed gax exception (so the gateway can map it)."""
    pricing_handle = MagicMock()

    def _stream_raise(**_: Any) -> Any:
        async def _gen():
            raise gax_exceptions.PermissionDenied("pricing-agent-sa lacks aiplatform.user")
            yield  # pragma: no cover  # makes mypy/ruff happy with async-gen shape

        return _gen()

    pricing_handle.async_stream_query = _stream_raise
    monkeypatch.setattr(a2a.agent_engines, "get", lambda resource: pricing_handle)

    with pytest.raises(gax_exceptions.PermissionDenied, match="aiplatform.user"):
        await a2a.call_peer_agent(
            peer="pricing",
            mode="price_listing",
            input={"category": "prepared_meal", "region": "US-FL", "units": 1,
                   "retail_value": 10, "hours_until_expiry": 4, "now_hour": 12,
                   "merchant_floor_pct": 0.10},
            partner_id="sk_demo",
        )
