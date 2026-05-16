"""Unit tests for `agents.listing_intake.tools`.

`parse_draft` is an echo tool: the LLM fills keyword args from free text,
and the tool validates shape and returns a structured dict. Tests here
pass kwargs directly (bypassing the LLM) to verify the echo contract and
the validation guard on empty text.
"""

from __future__ import annotations

from decimal import Decimal

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
    assert any(e["field"] == "units" for e in result["errors"])
