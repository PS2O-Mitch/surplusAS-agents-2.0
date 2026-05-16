"""ADK tools for the Listing Intake agent.

Four tools, all async, returning JSON-serialisable dicts. The flow:

    parse_draft(text, image_b64?) -> ListingDraft     (model fills, tool validates)
    validate_listing(draft)        -> ValidationResult
    request_anchor_price(draft, partner_id) -> RecommendationLogEntry  (lateral A2A -> Pricing)
    persist_listing(draft, recommendation_id, merchant_id) -> {"listing_id": ...}

Listings are NEVER saved without a recommendation. If pricing returns
`no_anchor`, the agent's prompt persists with status='draft_no_price'.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


async def parse_draft(
    *,
    text: str,
    image_b64: str | None = None,
    title: str | None = None,
    description: str | None = None,
    category: str | None = None,
    units: int | None = None,
    retail_value: float | None = None,
    hours_until_expiry: float | None = None,
    image_uri: str | None = None,
) -> dict[str, Any]:
    """Validate and echo a structured draft the model extracted from free text.

    The model is expected to fill the keyword args from `text` (and optionally
    a base64 image). This tool just validates shapes — actual NL extraction
    happens upstream in the model's reasoning step. Returning the parsed dict
    gives the trace a named span and lets the prompt's "after parse, call
    validate" rubric kick in.
    """
    if not text or not text.strip():
        return {
            "status": "validation_error",
            "field": "text",
            "error": "text must be a non-empty string",
        }

    retail = _decimal_or_none(retail_value)
    expiry = _decimal_or_none(hours_until_expiry)

    draft = {
        "title": title or "",
        "description": description,
        "category": category or "",
        "units": int(units) if units is not None else 0,
        "retail_value": str(retail) if retail is not None else None,
        "hours_until_expiry": str(expiry) if expiry is not None else None,
        "image_uri": image_uri,
    }
    return {"status": "ok", "draft": draft, "had_image": image_b64 is not None}
