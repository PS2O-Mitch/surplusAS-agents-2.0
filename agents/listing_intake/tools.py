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

from shared.pricing_intel import VALID_CATEGORIES


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
            "error": "text must be a non-empty string",
            "field": "text",
        }

    retail = _decimal_or_none(retail_value)
    expiry = _decimal_or_none(hours_until_expiry)

    draft = {
        "title": title,
        "description": description,
        "category": category,
        "units": int(units) if units is not None else None,
        "retail_value": str(retail) if retail is not None else None,
        "hours_until_expiry": str(expiry) if expiry is not None else None,
        "image_uri": image_uri,
    }
    return {"status": "ok", "draft": draft, "had_image": image_b64 is not None}


async def validate_listing(*, draft: dict[str, Any]) -> dict[str, Any]:
    """Validate a parsed ListingDraft against the schema + business rules.

    Pure-function check — no DB access. Returns a list of {field, error}
    so the model can fix problems one at a time.
    """
    errors: list[dict[str, str]] = []

    title = draft.get("title")
    if not title or not str(title).strip():
        errors.append({"field": "title", "error": "title is required"})

    category = draft.get("category")
    if not category:
        errors.append({"field": "category", "error": "category is required"})
    elif category not in VALID_CATEGORIES:
        errors.append({
            "field": "category",
            "error": f"unknown category {category!r}; "
                     f"valid options are {sorted(VALID_CATEGORIES)}",
        })

    units = draft.get("units")
    if units is None:
        errors.append({"field": "units", "error": "units is required"})
    else:
        try:
            units_int = int(units)
        except (TypeError, ValueError):
            units_int = 0
        if units_int < 1:
            errors.append({"field": "units", "error": "units must be >= 1"})

    retail = _decimal_or_none(draft.get("retail_value"))
    if retail is None or retail <= 0:
        errors.append({"field": "retail_value",
                       "error": "retail_value must be > 0"})

    expiry = _decimal_or_none(draft.get("hours_until_expiry"))
    if expiry is None or expiry < 0:
        errors.append({"field": "hours_until_expiry",
                       "error": "hours_until_expiry must be >= 0"})

    return {
        "status": "ok" if not errors else "validation_error",
        "errors": errors,
    }
