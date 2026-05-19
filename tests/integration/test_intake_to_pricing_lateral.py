"""Integration test: Listing Intake -> Pricing over A2A SDK (lateral edge).

The Pricing handle is mocked at the SDK boundary (`vertexai.agent_engines.get`),
so this runs in CI without GCP credentials. What it verifies:

1. `request_anchor_price` sends a **plain-string** request to Pricing (the
   shape ADK Runner expects) — not the legacy `{mode, input}` envelope.
2. The user_id forwarded to `async_stream_query` matches `partner_id`.
3. The stream aggregator extracts Pricing's narration verbatim and the
   tool returns `{status: ok, narration: ...}` upward to the Listing Intake
   model, which then quotes that narration in its merchant-facing reply.

Lateral edge per CLAUDE.md ("Listing Intake -> Pricing") — pinned here so
a regression in the wire shape fails CI even before a remote eval is run.
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
    yield
    get_settings.cache_clear()


async def test_intake_to_pricing_wire_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pricing must receive a plain-string message with the 7 pricing fields named."""
    captured: dict[str, Any] = {}

    # Pricing's stream — model invokes price_listing then narrates.
    pricing_events = [
        {"content": {"parts": [{"function_call": {
            "name": "price_listing",
            "args": {"category": "prepared_meal", "region": "US-FL-Hillsborough",
                     "units": 10, "retail_value": 12.00, "hours_until_expiry": 4.0,
                     "now_hour": 18, "merchant_floor_pct": 0.10,
                     "partner_id": "sk_demo_surplus_2026"},
        }}], "role": "model"}, "author": "pricing"},
        {"content": {"parts": [{"function_response": {
            "name": "price_listing",
            "response": {"status": "ok", "recommendation": {
                "recommendation_id": "11111111-1111-1111-1111-111111111111",
                "recommended_price": 7.25, "formula_version": "v1",
                "applied_pressures": {
                    "base": 0.10, "expiry": 0.30, "inventory": 0.05,
                    "time_of_day": 0.05, "merchant_floor": 0.10,
                    "clamped_to_floor": False, "clamped_to_retail": False,
                },
            }},
        }}], "role": "user"}, "author": "pricing"},
        {"content": {"parts": [{"text":
            "Priced at $7.25 — expiry pressure dominant at 0.30."}],
            "role": "model"}, "author": "pricing"},
    ]

    pricing_handle = MagicMock()

    def _async_stream_query(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _make_async_iter(pricing_events)

    pricing_handle.async_stream_query = _async_stream_query
    monkeypatch.setattr(a2a.agent_engines, "get", lambda _r: pricing_handle)

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

    # Pricing receives a STRING (not a dict envelope).
    msg = captured["message"]
    assert isinstance(msg, str)
    assert "price_listing" in msg
    assert "Category: prepared_meal" in msg
    assert "Region: US-FL-Hillsborough" in msg
    assert "Units: 10" in msg
    assert "Retail value: $12.0" in msg
    assert "Hours until expiry: 4.0" in msg
    assert "Current hour (24h): 18" in msg
    assert "Merchant floor pct: 0.1" in msg
    assert "Partner id: sk_demo_surplus_2026" in msg
    assert captured["user_id"] == "sk_demo_surplus_2026"

    # Listing Intake gets back a structured payload it can quote.
    assert out["status"] == "ok"
    assert "Priced at $7.25" in out["narration"]
    assert "0.30" in out["narration"]


async def test_intake_no_anchor_relays_narration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Pricing surfaces no_anchor in its narration, Listing Intake sees it."""
    pricing_handle = MagicMock()
    pricing_handle.async_stream_query = lambda **_: _make_async_iter([
        {"content": {"parts": [{"text":
            "No reference price for category=prepared_meal region=ZZ — "
            "listing parked as draft_no_price."}],
            "role": "model"}, "author": "pricing"},
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
    assert out["status"] == "ok"
    assert "draft_no_price" in out["narration"]
