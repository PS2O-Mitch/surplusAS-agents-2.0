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
from uuid import UUID

from shared import a2a
from shared.db import fetch_one, init_pool
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


async def request_anchor_price(
    *,
    draft: dict[str, Any],
    partner_id: str,
    region: str,
    merchant_floor_pct: float,
    now_hour: int,
) -> dict[str, Any]:
    """Call Pricing over A2A to anchor this draft to a live recommendation.

    Lateral edge per CLAUDE.md ("Listing Intake -> Pricing"). The draft hasn't
    been persisted yet — Pricing will write a `recommendation_log` row with
    `listing_id=NULL`, and `persist_listing` will then row-bind it.

    Sends a plain-English request to Pricing (the shape its prompt knows how
    to translate into `price_listing(...)`) and aggregates the stream. The
    returned `narration` is Pricing's own summary of `applied_pressures` and
    the recommended price; the Listing Intake model surfaces it verbatim
    rather than re-deriving anything.
    """
    retail = _decimal_or_none(draft.get("retail_value"))
    expiry = _decimal_or_none(draft.get("hours_until_expiry"))
    if retail is None or expiry is None:
        return {
            "status": "validation_error",
            "error": "draft must have numeric retail_value and hours_until_expiry "
                     "before pricing",
        }

    user_message = (
        "Please price this listing using the price_listing tool. "
        f"Category: {draft['category']}. Region: {region}. "
        f"Units: {int(draft['units'])}. Retail value: ${float(retail)}. "
        f"Hours until expiry: {float(expiry)}. "
        f"Current hour (24h): {int(now_hour)}. "
        f"Merchant floor pct: {float(merchant_floor_pct)}. "
        f"Partner id: {partner_id}."
    )
    agg = await a2a.aggregate_peer_stream("pricing", user_message, partner_id)
    return {"status": "ok", "narration": agg["narration"]}


_VALID_STATUSES = {"draft", "draft_no_price", "published"}


async def persist_listing(
    *,
    draft: dict[str, Any],
    recommendation_id: str,
    merchant_id: str,
    partner_id: str,
    status: str = "draft",
) -> dict[str, Any]:
    """Insert a row into `agents.listings`, binding to the recommendation.

    Caller must have run `validate_listing` first — this tool does not
    re-validate the draft fields, only the status + UUIDs + presence of
    numeric retail_value / hours_until_expiry.

    Status semantics:
      - 'draft'           -> had a live anchor; ready to publish.
      - 'draft_no_price'  -> Pricing returned no_anchor; surfaced to Concierge.
      - 'published'       -> Listing Intake's publish helper (called by demo shim).

    Both `initial_recommendation_id` and `current_recommendation_id` are set
    to the same value at insert time. Dispute Triage will UPDATE
    `current_recommendation_id` to a replay row but never touches the
    `initial_*` column — that's the audit anchor.
    """
    if status not in _VALID_STATUSES:
        return {
            "status": "validation_error",
            "error": f"status must be one of {sorted(_VALID_STATUSES)}",
            "field": "status",
        }

    try:
        rec_uuid = UUID(recommendation_id)
        merch_uuid = UUID(merchant_id)
    except ValueError as exc:
        return {
            "status": "validation_error",
            "error": f"invalid UUID: {exc}",
            "field": "uuid",
        }

    retail = _decimal_or_none(draft.get("retail_value"))
    expiry = _decimal_or_none(draft.get("hours_until_expiry"))
    if retail is None or expiry is None:
        return {
            "status": "validation_error",
            "error": "draft is missing retail_value or hours_until_expiry",
            "field": "draft",
        }

    await init_pool()
    row = await fetch_one(
        "INSERT INTO agents.listings "
        "  (merchant_id, partner_id, title, description, category, units, "
        "   retail_value, hours_until_expiry, image_uri, status, "
        "   initial_recommendation_id, current_recommendation_id) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$11) "
        "RETURNING listing_id",
        merch_uuid,
        partner_id,
        draft["title"],
        draft.get("description"),
        draft["category"],
        int(draft["units"]),
        retail,
        expiry,
        draft.get("image_uri"),
        status,
        rec_uuid,
    )
    assert row is not None
    return {
        "status": "ok",
        "listing_id": str(row["listing_id"]),
        "recommendation_id": recommendation_id,
        "merchant_id": merchant_id,
        "listing_status": status,
    }
