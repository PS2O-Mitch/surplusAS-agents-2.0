"""Unit tests for `agents.listing_intake.tools`.

`parse_draft` is an echo tool: the LLM fills keyword args from free text,
and the tool validates shape and returns a structured dict. Tests here
pass kwargs directly (bypassing the LLM) to verify the echo contract and
the validation guard on empty text.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    import pytest

from agents.listing_intake.tools import parse_draft, validate_listing


async def test_parse_draft_echoes_provided_fields() -> None:
    result = await parse_draft(
        text="10 turkey sandwiches, expire in 4 hours, normally $12 each",
        title="Day-old turkey sandwiches",
        category="prepared_meal",
        units=10,
        retail_value=12.0,
        hours_until_expiry=4.0,
    )
    assert result["status"] == "ok"
    draft = result["draft"]
    assert draft["title"] == "Day-old turkey sandwiches"
    assert draft["category"] == "prepared_meal"
    assert draft["units"] == 10
    assert Decimal(str(draft["retail_value"])) == Decimal("12.00")
    assert Decimal(str(draft["hours_until_expiry"])) == Decimal("4")


async def test_parse_draft_returns_validation_error_on_empty_text() -> None:
    result = await parse_draft(text="")
    assert result["status"] == "validation_error"
    assert result["field"] == "text"


async def test_parse_draft_with_only_text_returns_all_none_fields() -> None:
    """When the model passes raw text without extracting fields, the tool echoes
    None for every optional field so validate_listing can distinguish 'absent'
    from 'present-but-empty'."""
    result = await parse_draft(text="10 turkey sandwiches expire in 4h")
    assert result["status"] == "ok"
    draft = result["draft"]
    assert draft["title"] is None
    assert draft["category"] is None
    assert draft["units"] is None
    assert draft["retail_value"] is None
    assert draft["hours_until_expiry"] is None
    assert draft["description"] is None
    assert draft["image_uri"] is None
    assert result["had_image"] is False


async def test_validate_listing_ok_for_complete_draft() -> None:
    draft = {
        "title": "Day-old turkey sandwiches",
        "description": "house-made, refrigerated",
        "category": "prepared_meal",
        "units": 10,
        "retail_value": "12.00",
        "hours_until_expiry": "4",
        "image_uri": None,
    }
    result = await validate_listing(draft=draft)
    assert result["status"] == "ok"
    assert result["errors"] == []


async def test_validate_listing_rejects_unknown_category() -> None:
    draft = {
        "title": "x", "category": "unicorn_food", "units": 1,
        "retail_value": "10", "hours_until_expiry": "4",
    }
    result = await validate_listing(draft=draft)
    assert result["status"] == "validation_error"
    assert any(e["field"] == "category" for e in result["errors"])


async def test_validate_listing_rejects_zero_units() -> None:
    draft = {
        "title": "x", "category": "prepared_meal", "units": 0,
        "retail_value": "10", "hours_until_expiry": "4",
    }
    result = await validate_listing(draft=draft)
    assert result["status"] == "validation_error"
    units_errors = [e for e in result["errors"] if e["field"] == "units"]
    assert len(units_errors) == 1
    assert units_errors[0]["error"] == "units must be >= 1"


async def test_validate_listing_rejects_missing_units() -> None:
    draft = {
        "title": "x", "category": "prepared_meal",
        "retail_value": "10", "hours_until_expiry": "4",
        # units absent on purpose
    }
    result = await validate_listing(draft=draft)
    assert result["status"] == "validation_error"
    units_errors = [e for e in result["errors"] if e["field"] == "units"]
    assert len(units_errors) == 1
    assert units_errors[0]["error"] == "units is required"


async def test_request_anchor_price_calls_pricing_with_intake_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_call_peer_agent(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "status": "ok",
            "recommendation": {
                "recommendation_id": "11111111-1111-1111-1111-111111111111",
                "recommended_price": 7.25,
                "applied_pressures": {"base": 0.10, "clamped_to_floor": False},
                "formula_version": "v1",
            },
        }

    from agents.listing_intake import tools as intake_tools
    monkeypatch.setattr(intake_tools.a2a, "call_peer_agent", fake_call_peer_agent)

    draft = {
        "title": "Day-old sandwiches",
        "category": "prepared_meal",
        "units": 10,
        "retail_value": "12.00",
        "hours_until_expiry": "4",
    }
    result = await intake_tools.request_anchor_price(
        draft=draft,
        partner_id="sk_demo",
        region="US-FL-Hillsborough",
        merchant_floor_pct=0.10,
        now_hour=18,
    )

    assert captured["peer"] == "pricing"
    assert captured["mode"] == "price_listing"
    assert captured["partner_id"] == "sk_demo"
    pricing_input = captured["input"]
    assert pricing_input["category"] == "prepared_meal"
    assert pricing_input["region"] == "US-FL-Hillsborough"
    assert pricing_input["units"] == 10
    assert float(pricing_input["retail_value"]) == 12.00
    assert float(pricing_input["hours_until_expiry"]) == 4.0
    assert pricing_input["merchant_floor_pct"] == 0.10
    assert pricing_input["now_hour"] == 18

    assert result["status"] == "ok"
    assert result["recommendation"]["recommended_price"] == 7.25


async def test_request_anchor_price_surfaces_no_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents.listing_intake import tools as intake_tools

    async def fake_call_peer_agent(**_: Any) -> dict[str, Any]:
        return {"status": "no_anchor",
                "narration": "No reference price for category=x region=ZZ"}

    monkeypatch.setattr(intake_tools.a2a, "call_peer_agent", fake_call_peer_agent)

    result = await intake_tools.request_anchor_price(
        draft={"title": "x", "category": "prepared_meal", "units": 1,
               "retail_value": "10", "hours_until_expiry": "4"},
        partner_id="sk_demo",
        region="ZZ",
        merchant_floor_pct=0.10,
        now_hour=12,
    )
    assert result["status"] == "no_anchor"
    assert "No reference price" in result["narration"]


async def test_persist_listing_inserts_with_recommendation_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agents.listing_intake import tools as intake_tools

    captured_sql: list[tuple[str, tuple[Any, ...]]] = []

    async def fake_fetch_one(sql: str, *args: Any) -> dict[str, Any]:
        captured_sql.append((sql, args))
        return {"listing_id": "22222222-2222-2222-2222-222222222222"}

    class _FakePool:
        def acquire(self) -> Any:
            class _Ctx:
                async def __aenter__(self_inner) -> Any:
                    return None
                async def __aexit__(self_inner, *_: Any) -> None: ...
            return _Ctx()

    monkeypatch.setattr(intake_tools, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(intake_tools, "init_pool",
                        AsyncMock(return_value=_FakePool()))

    result = await intake_tools.persist_listing(
        draft={
            "title": "Day-old sandwiches",
            "description": "deli-made",
            "category": "prepared_meal",
            "units": 10,
            "retail_value": "12.00",
            "hours_until_expiry": "4",
            "image_uri": None,
        },
        recommendation_id="11111111-1111-1111-1111-111111111111",
        merchant_id="33333333-3333-3333-3333-333333333333",
        partner_id="sk_demo",
        status="draft",
    )
    assert result["status"] == "ok"
    assert result["listing_id"] == "22222222-2222-2222-2222-222222222222"

    sql, _args = captured_sql[0]
    assert "INSERT INTO agents.listings" in sql
    assert "initial_recommendation_id" in sql
    assert "current_recommendation_id" in sql


async def test_persist_listing_rejects_invalid_status() -> None:
    from agents.listing_intake.tools import persist_listing

    result = await persist_listing(
        draft={"title": "x", "category": "prepared_meal", "units": 1,
               "retail_value": "10", "hours_until_expiry": "4"},
        recommendation_id="11111111-1111-1111-1111-111111111111",
        merchant_id="33333333-3333-3333-3333-333333333333",
        partner_id="sk_demo",
        status="bogus",
    )
    assert result["status"] == "validation_error"
    assert result["field"] == "status"
