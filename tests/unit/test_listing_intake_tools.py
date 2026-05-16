"""Unit tests for `agents.listing_intake.tools`.

`parse_draft` is an echo tool: the LLM fills keyword args from free text,
and the tool validates shape and returns a structured dict. Tests here
pass kwargs directly (bypassing the LLM) to verify the echo contract and
the validation guard on empty text.
"""

from __future__ import annotations

from decimal import Decimal

from agents.listing_intake.tools import parse_draft


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
