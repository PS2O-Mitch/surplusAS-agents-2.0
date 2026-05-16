"""Unit tests for `agents.concierge.tools`.

Each routing tool wraps `shared.a2a.aggregate_peer_stream` and returns a
structured `{status, narration}` dict the Concierge model can quote.
Tests monkeypatch the aggregator so no SDK / GCP access is needed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

from agents.concierge import tools as concierge_tools


def _patch_aggregator(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch `aggregate_peer_stream` and return a captured kwargs dict."""
    captured: dict[str, Any] = {}

    async def fake_aggregate(peer: Any, user_message: str, partner_id: str,
                              *, session_id: Any = None) -> dict[str, Any]:
        captured["peer"] = peer
        captured["user_message"] = user_message
        captured["partner_id"] = partner_id
        captured["session_id"] = session_id
        return {
            "narration": f"({peer} narration for: {user_message[:60]})",
            "tool_calls": [],
            "event_count": 1,
        }

    monkeypatch.setattr(concierge_tools.a2a, "aggregate_peer_stream", fake_aggregate)
    return captured


async def test_route_to_onboarding_sends_plain_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_aggregator(monkeypatch)
    out = await concierge_tools.route_to_onboarding(
        message="I'm a deli in Tampa",
        partner_id="sk_demo",
    )
    assert captured["peer"] == "onboarding"
    assert captured["user_message"] == "I'm a deli in Tampa"
    assert captured["partner_id"] == "sk_demo"
    assert out["status"] == "ok"
    assert out["narration"].startswith("(onboarding narration for")


async def test_route_to_onboarding_includes_merchant_id_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_aggregator(monkeypatch)
    await concierge_tools.route_to_onboarding(
        message="change my floor", partner_id="sk_demo", merchant_id="m-1",
    )
    assert captured["user_message"] == "[merchant_id=m-1] change my floor"


async def test_route_to_listing_intake_marks_image_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_aggregator(monkeypatch)
    out = await concierge_tools.route_to_listing_intake(
        message="see photo, 10 sandwiches", partner_id="sk_demo",
        image_b64="iVBORw0KGgo=",
    )
    assert captured["peer"] == "listing_intake"
    assert "image_attached=true" in captured["user_message"]
    assert out["status"] == "ok"


async def test_route_to_pricing_serialises_input_to_english(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_aggregator(monkeypatch)
    await concierge_tools.route_to_pricing(
        pricing_input_json={
            "category": "prepared_meal", "region": "US-FL", "units": 1,
            "retail_value": 12.0, "hours_until_expiry": 4.0, "now_hour": 18,
            "merchant_floor_pct": 0.10,
        },
        partner_id="sk_demo",
    )
    msg = captured["user_message"]
    assert captured["peer"] == "pricing"
    assert "price_listing" in msg
    assert "Category: prepared_meal" in msg
    assert "Region: US-FL" in msg
    assert "Units: 1" in msg
    assert "Retail value: $12.0" in msg
    assert "Hours until expiry: 4.0" in msg
    assert "Current hour (24h): 18" in msg
    assert "Merchant floor pct: 0.1" in msg
    assert "Partner id: sk_demo" in msg


async def test_route_to_dispute_triage_includes_listing_id_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_aggregator(monkeypatch)
    await concierge_tools.route_to_dispute_triage(
        listing_id="22222222-2222-2222-2222-222222222222",
        dispute_text="price moved too fast",
        partner_id="sk_demo",
    )
    assert captured["peer"] == "dispute_triage"
    assert "listing_id=22222222-2222-2222-2222-222222222222" in captured["user_message"]
    assert "price moved too fast" in captured["user_message"]
