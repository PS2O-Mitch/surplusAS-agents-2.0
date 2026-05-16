"""Unit tests for `agents.concierge.tools`.

Each routing tool is a thin wrapper over `shared.a2a.call_peer_agent`.
Tests monkeypatch the patched-in `a2a` module attribute and verify the
wire shape (peer name, mode, partner_id, input keys) for each route.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

from agents.concierge import tools as concierge_tools


def _make_capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def fake_call_peer_agent(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"status": "ok", "echoed_peer": kwargs["peer"]}

    monkeypatch.setattr(concierge_tools.a2a, "call_peer_agent", fake_call_peer_agent)
    return captured


async def test_route_to_onboarding_packs_message(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _make_capture(monkeypatch)
    out = await concierge_tools.route_to_onboarding(
        message="I'm a deli in Tampa, do produce and prepared meals",
        partner_id="sk_demo",
    )
    assert captured["peer"] == "onboarding"
    assert captured["mode"] == "process"
    assert captured["partner_id"] == "sk_demo"
    assert captured["input"]["message"].startswith("I'm a deli")
    assert captured["input"]["merchant_id"] is None
    assert out["status"] == "ok"


async def test_route_to_listing_intake_carries_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _make_capture(monkeypatch)
    out = await concierge_tools.route_to_listing_intake(
        message="10 turkey sandwiches expire in 4h",
        partner_id="sk_demo",
        image_b64="iVBORw0KGgo=",
    )
    assert captured["peer"] == "listing_intake"
    assert captured["mode"] == "process"
    assert captured["input"]["image_b64"] == "iVBORw0KGgo="
    assert out["echoed_peer"] == "listing_intake"


async def test_route_to_pricing_forwards_pricing_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _make_capture(monkeypatch)
    pricing_input = {
        "category": "prepared_meal", "region": "US-FL", "units": 1,
        "retail_value": 12.0, "hours_until_expiry": 4.0, "now_hour": 18,
        "merchant_floor_pct": 0.10,
    }
    await concierge_tools.route_to_pricing(
        pricing_input_json=pricing_input,
        partner_id="sk_demo",
    )
    assert captured["peer"] == "pricing"
    assert captured["mode"] == "price_listing"
    assert captured["input"] == pricing_input


async def test_route_to_dispute_triage(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _make_capture(monkeypatch)
    await concierge_tools.route_to_dispute_triage(
        listing_id="22222222-2222-2222-2222-222222222222",
        dispute_text="price moved too fast",
        partner_id="sk_demo",
    )
    assert captured["peer"] == "dispute_triage"
    assert captured["mode"] == "resolve"
    assert captured["input"]["listing_id"] == "22222222-2222-2222-2222-222222222222"
    assert captured["input"]["reason"] == "price moved too fast"
