from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    import pytest

from agents.dispute_triage.tools import fetch_recommendation_log


async def test_fetch_recommendation_log_returns_latest_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = {
        "recommendation_id": "11111111-1111-1111-1111-111111111111",
        "listing_id": "22222222-2222-2222-2222-222222222222",
        "merchant_id": "33333333-3333-3333-3333-333333333333",
        "partner_id": "sk_demo",
        "pricing_input": {"category": "prepared_meal", "region": "US-FL",
                           "units": 10, "retail_value": 12.00,
                           "hours_until_expiry": 4.0, "now_hour": 18,
                           "merchant_floor_pct": 0.10},
        "recommended_price": 7.25,
        "recommended_discount_pct": 0.40,
        "anchor_p50": 11.50,
        "anchor_source": "apify",
        "anchor_region": "US-FL",
        "applied_pressures": {"base": 0.10, "expiry": 0.30,
                              "clamped_to_floor": False},
        "formula_version": "v1",
        "coefficients_version": 7,
        "replay_of": None,
    }
    from agents.dispute_triage import tools as dt
    monkeypatch.setattr(dt, "fetch_one", AsyncMock(return_value=expected))
    monkeypatch.setattr(dt, "init_pool", AsyncMock())

    result = await fetch_recommendation_log(
        listing_id="22222222-2222-2222-2222-222222222222",
    )
    assert result["status"] == "ok"
    assert result["recommendation"]["recommended_price"] == 7.25
    assert result["recommendation"]["applied_pressures"]["expiry"] == 0.30


async def test_fetch_recommendation_log_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents.dispute_triage import tools as dt
    monkeypatch.setattr(dt, "fetch_one", AsyncMock(return_value=None))
    monkeypatch.setattr(dt, "init_pool", AsyncMock())

    result = await fetch_recommendation_log(
        listing_id="22222222-2222-2222-2222-222222222222",
    )
    assert result["status"] == "not_found"
    assert "listing_id" in result["error"]


async def test_fetch_recommendation_log_rejects_invalid_uuid() -> None:
    """If the listing_id isn't a valid UUID, fail fast without touching the DB."""
    from agents.dispute_triage.tools import fetch_recommendation_log
    result = await fetch_recommendation_log(listing_id="not-a-uuid")
    assert result["status"] == "validation_error"
    assert result["field"] == "listing_id"
