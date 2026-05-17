from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from agents.dispute_triage.tools import (
    diff_pressures,
    emit_price_update_webhook,
    fetch_recommendation_log,
    persist_dispute,
    request_reprice,
)


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


async def test_diff_pressures_signed_per_key() -> None:
    old = {"base": 0.10, "expiry": 0.08, "inventory": 0.05,
           "time_of_day": 0.05, "merchant_floor": 0.10,
           "clamped_to_floor": False, "clamped_to_retail": False}
    new = {"base": 0.10, "expiry": 0.21, "inventory": 0.05,
           "time_of_day": 0.05, "merchant_floor": 0.10,
           "clamped_to_floor": False, "clamped_to_retail": False}
    diff = await diff_pressures(old=old, new=new)
    assert diff["status"] == "ok"
    deltas = diff["deltas"]
    assert deltas["expiry"] == pytest.approx(0.13)
    assert deltas["base"] == 0.0
    # Boolean pressures: reported as -1/0/+1 transition signal
    assert deltas["clamped_to_floor"] == 0


async def test_diff_pressures_handles_clamp_transition() -> None:
    old = {"base": 0.10, "clamped_to_floor": False, "clamped_to_retail": False}
    new = {"base": 0.10, "clamped_to_floor": True,  "clamped_to_retail": False}
    diff = await diff_pressures(old=old, new=new)
    assert diff["deltas"]["clamped_to_floor"] == 1
    assert diff["deltas"]["clamped_to_retail"] == 0


async def test_diff_pressures_treats_missing_keys_as_zero() -> None:
    """Keys present in only one map carry the present value as delta."""
    old = {"base": 0.10}
    new = {"base": 0.10, "expiry": 0.20}
    diff = await diff_pressures(old=old, new=new)
    assert diff["deltas"]["expiry"] == pytest.approx(0.20)
    assert diff["deltas"]["base"] == 0.0


async def test_request_reprice_extracts_replay_tool_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """request_reprice must pull the structured replay_recommendation result
    out of the Pricing stream's tool_calls (not just relay narration)."""
    captured: dict[str, Any] = {}

    async def fake_aggregate(peer, user_message, partner_id, *, session_id=None):
        captured["peer"] = peer
        captured["user_message"] = user_message
        captured["partner_id"] = partner_id
        return {
            "narration": "Replayed under fresh coefficients: new price $6.50, "
                         "expiry pressure now 0.21.",
            "tool_calls": [{
                "name": "replay_recommendation",
                "args": {"recommendation_id":
                         "11111111-1111-1111-1111-111111111111",
                         "partner_id": "sk_demo"},
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
            }],
            "event_count": 4,
        }

    from agents.dispute_triage import tools as dt
    monkeypatch.setattr(dt.a2a, "aggregate_peer_stream", fake_aggregate)

    out = await request_reprice(
        original_recommendation_id="11111111-1111-1111-1111-111111111111",
        partner_id="sk_demo",
    )
    assert captured["peer"] == "pricing"
    assert "replay_recommendation" in captured["user_message"]
    assert "11111111-1111-1111-1111-111111111111" in captured["user_message"]
    assert captured["partner_id"] == "sk_demo"
    assert out["status"] == "ok"
    assert out["new_recommendation_id"] == "55555555-5555-5555-5555-555555555555"
    assert out["new_price"] == 6.50
    assert out["new_pressures"]["expiry"] == 0.21
    assert "6.50" in out["narration"]


async def test_request_reprice_returns_error_when_tool_response_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If Pricing's stream lacks a replay_recommendation function_response, we
    can't derive the new id — surface a clean error rather than fabricating."""
    async def fake_aggregate(*_: Any, **__: Any) -> dict[str, Any]:
        return {"narration": "...", "tool_calls": [], "event_count": 1}

    from agents.dispute_triage import tools as dt
    monkeypatch.setattr(dt.a2a, "aggregate_peer_stream", fake_aggregate)

    out = await request_reprice(
        original_recommendation_id="11111111-1111-1111-1111-111111111111",
        partner_id="sk_demo",
    )
    assert out["status"] == "error"
    assert "replay_recommendation" in out["error"]


async def test_request_reprice_rejects_invalid_uuid() -> None:
    result = await request_reprice(
        original_recommendation_id="not-a-uuid",
        partner_id="sk_demo",
    )
    assert result["status"] == "validation_error"
    assert result["field"] == "recommendation_id"


async def test_persist_dispute_inserts_row(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_sql: list[tuple[str, tuple[Any, ...]]] = []

    async def fake_fetch_one(sql: str, *args: Any) -> dict[str, Any]:
        captured_sql.append((sql, args))
        return {"dispute_id": "44444444-4444-4444-4444-444444444444"}

    from agents.dispute_triage import tools as dt
    monkeypatch.setattr(dt, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(dt, "init_pool", AsyncMock())

    out = await persist_dispute(
        listing_id="22222222-2222-2222-2222-222222222222",
        merchant_id="33333333-3333-3333-3333-333333333333",
        partner_id="sk_demo",
        reason_text="price moved too fast",
        original_recommendation_id="11111111-1111-1111-1111-111111111111",
        new_recommendation_id="55555555-5555-5555-5555-555555555555",
        pressure_diff={"expiry": 0.13, "clamped_to_floor": 0},
    )
    assert out["status"] == "ok"
    assert out["dispute_id"] == "44444444-4444-4444-4444-444444444444"

    sql, args = captured_sql[0]
    assert "INSERT INTO agents.disputes" in sql
    assert "pressure_diff" in sql
    # pressure_diff must be JSON-serialised before binding
    assert any("expiry" in repr(a) for a in args)


async def test_persist_dispute_rejects_invalid_uuid() -> None:
    out = await persist_dispute(
        listing_id="not-a-uuid",
        merchant_id="33333333-3333-3333-3333-333333333333",
        partner_id="sk_demo",
        reason_text="x",
        original_recommendation_id="11111111-1111-1111-1111-111111111111",
        new_recommendation_id="55555555-5555-5555-5555-555555555555",
        pressure_diff={},
    )
    assert out["status"] == "validation_error"
    assert out["field"] == "uuid"


async def test_persist_dispute_rejects_empty_reason() -> None:
    out = await persist_dispute(
        listing_id="22222222-2222-2222-2222-222222222222",
        merchant_id="33333333-3333-3333-3333-333333333333",
        partner_id="sk_demo",
        reason_text="   ",  # whitespace-only
        original_recommendation_id="11111111-1111-1111-1111-111111111111",
        new_recommendation_id="55555555-5555-5555-5555-555555555555",
        pressure_diff={},
    )
    assert out["status"] == "validation_error"
    assert out["field"] == "reason_text"


async def test_emit_price_update_webhook_calls_emit_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_emit_event(*, event_type: str, partner_id: str,
                               payload: dict[str, Any]) -> dict[str, Any]:
        captured["event_type"] = event_type
        captured["partner_id"] = partner_id
        captured["payload"] = payload
        return {"status": "ok",
                "delivery_ids": ["11111111-1111-1111-1111-111111111111"]}

    from agents.dispute_triage import tools as dt
    monkeypatch.setattr(dt, "emit_event", fake_emit_event)

    out = await emit_price_update_webhook(
        partner_id="sk_demo",
        listing_id="22222222-2222-2222-2222-222222222222",
        old_price=7.25, new_price=6.50,
        new_recommendation_id="55555555-5555-5555-5555-555555555555",
    )
    assert captured["event_type"] == "price.updated"
    assert captured["partner_id"] == "sk_demo"
    assert captured["payload"]["listing_id"] == \
        "22222222-2222-2222-2222-222222222222"
    assert captured["payload"]["old_price"] == 7.25
    assert captured["payload"]["new_price"] == 6.50
    assert captured["payload"]["new_recommendation_id"] == \
        "55555555-5555-5555-5555-555555555555"
    assert out["status"] == "ok"
    assert out["delivery_ids"] == ["11111111-1111-1111-1111-111111111111"]


async def test_emit_price_update_webhook_skips_under_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """|7.25 - 7.10| = 0.15 <= 0.25 — emit_event must NOT be called."""
    called = False

    async def fake_emit_event(**_: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"status": "ok", "delivery_ids": []}

    from agents.dispute_triage import tools as dt
    monkeypatch.setattr(dt, "emit_event", fake_emit_event)

    out = await emit_price_update_webhook(
        partner_id="sk_demo",
        listing_id="22222222-2222-2222-2222-222222222222",
        old_price=7.25, new_price=7.10,
        new_recommendation_id="55555555-5555-5555-5555-555555555555",
    )
    assert out["status"] == "skipped"
    assert "threshold" in out["reason"].lower()
    assert called is False


async def test_emit_price_update_webhook_fires_at_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At exactly $0.25 delta, the threshold is `> 0.25` so it should NOT fire."""
    called = False

    async def fake_emit_event(**_: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"status": "ok", "delivery_ids": []}

    from agents.dispute_triage import tools as dt
    monkeypatch.setattr(dt, "emit_event", fake_emit_event)

    out = await emit_price_update_webhook(
        partner_id="sk_demo",
        listing_id="22222222-2222-2222-2222-222222222222",
        old_price=7.50, new_price=7.25,  # exactly 0.25 delta
        new_recommendation_id="55555555-5555-5555-5555-555555555555",
    )
    assert out["status"] == "skipped"
    assert called is False
