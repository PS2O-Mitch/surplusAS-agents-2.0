"""ADK tools for the Onboarding agent.

Four tools, all async, returning JSON-serialisable dicts so ADK's
auto-extractor can derive a clean tool-result schema.

- `create_merchant_profile` — single insert into `agents.merchant_profiles`.
  Returns the new `merchant_id`.
- `set_floor_pct` / `set_categories` / `set_region` — narrow UPDATEs for
  mid-conversation amendments. Each touches `updated_at` so downstream
  caches can invalidate cleanly.

Validation lives in here (not just in the DB CHECK) so the LLM gets a
descriptive error string it can recover from in-prompt instead of an
opaque Postgres CHECK violation.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from shared.db import execute, fetch_one
from shared.pricing_intel import VALID_CATEGORIES


def _validate_categories(allowed_categories: list[str]) -> str | None:
    """Return None when the list is valid, else an error message."""
    if not allowed_categories:
        return "allowed_categories must be a non-empty list"
    invalid = [c for c in allowed_categories if c not in VALID_CATEGORIES]
    if invalid:
        return (
            f"unknown categories {invalid!r}; "
            f"valid options are {sorted(VALID_CATEGORIES)}"
        )
    return None


def _validate_floor_pct(value: float) -> str | None:
    if not (0.0 <= value <= 1.0):
        return f"merchant_floor_pct must be between 0 and 1, got {value!r}"
    return None


def _validate_region(value: str) -> str | None:
    if not value or not value.strip():
        return "region must be a non-empty string"
    return None


async def create_merchant_profile(
    *,
    partner_id: str,
    merchant_name: str,
    region: str,
    allowed_categories: list[str],
    merchant_floor_pct: float = 0.10,
    timezone: str = "America/New_York",
) -> dict[str, Any]:
    """Insert a new merchant profile and return the assigned `merchant_id`.

    The merchant_id is generated server-side (`gen_random_uuid()` default
    on the column). We `RETURNING` it so the caller can chain into
    Listing Intake without a follow-up SELECT.
    """
    if (err := _validate_region(region)):
        return {"status": "validation_error", "error": err, "field": "region"}
    if (err := _validate_categories(allowed_categories)):
        return {
            "status": "validation_error",
            "error": err,
            "field": "allowed_categories",
        }
    if (err := _validate_floor_pct(merchant_floor_pct)):
        return {
            "status": "validation_error",
            "error": err,
            "field": "merchant_floor_pct",
        }

    row = await fetch_one(
        "INSERT INTO agents.merchant_profiles "
        "(partner_id, merchant_name, region, merchant_floor_pct, "
        " allowed_categories, timezone) "
        "VALUES ($1, $2, $3, $4, $5, $6) "
        "RETURNING merchant_id, created_at",
        partner_id,
        merchant_name,
        region,
        merchant_floor_pct,
        list(allowed_categories),
        timezone,
    )
    assert row is not None, "RETURNING is guaranteed to produce one row"
    return {
        "status": "ok",
        "merchant_id": str(row["merchant_id"]),
        "created_at": row["created_at"].isoformat(),
        "merchant_name": merchant_name,
        "region": region,
        "allowed_categories": list(allowed_categories),
        "merchant_floor_pct": merchant_floor_pct,
        "timezone": timezone,
    }


async def set_floor_pct(merchant_id: str, merchant_floor_pct: float) -> dict[str, Any]:
    """Update only `merchant_floor_pct` for an existing merchant."""
    if (err := _validate_floor_pct(merchant_floor_pct)):
        return {
            "status": "validation_error",
            "error": err,
            "field": "merchant_floor_pct",
        }
    try:
        mid = UUID(merchant_id)
    except ValueError:
        return {
            "status": "validation_error",
            "error": f"merchant_id {merchant_id!r} is not a valid UUID",
            "field": "merchant_id",
        }
    result = await execute(
        "UPDATE agents.merchant_profiles "
        "SET merchant_floor_pct = $2, updated_at = NOW() "
        "WHERE merchant_id = $1",
        mid,
        merchant_floor_pct,
    )
    if not result.endswith(" 1"):
        return {
            "status": "validation_error",
            "error": f"merchant_id {merchant_id!r} not found",
            "field": "merchant_id",
        }
    return {
        "status": "ok",
        "merchant_id": merchant_id,
        "merchant_floor_pct": merchant_floor_pct,
    }


async def set_categories(merchant_id: str, allowed_categories: list[str]) -> dict[str, Any]:
    """Replace the whole `allowed_categories` array for an existing merchant."""
    if (err := _validate_categories(allowed_categories)):
        return {
            "status": "validation_error",
            "error": err,
            "field": "allowed_categories",
        }
    try:
        mid = UUID(merchant_id)
    except ValueError:
        return {
            "status": "validation_error",
            "error": f"merchant_id {merchant_id!r} is not a valid UUID",
            "field": "merchant_id",
        }
    result = await execute(
        "UPDATE agents.merchant_profiles "
        "SET allowed_categories = $2, updated_at = NOW() "
        "WHERE merchant_id = $1",
        mid,
        list(allowed_categories),
    )
    if not result.endswith(" 1"):
        return {
            "status": "validation_error",
            "error": f"merchant_id {merchant_id!r} not found",
            "field": "merchant_id",
        }
    return {
        "status": "ok",
        "merchant_id": merchant_id,
        "allowed_categories": list(allowed_categories),
    }


async def set_region(merchant_id: str, region: str) -> dict[str, Any]:
    """Update only `region` for an existing merchant."""
    if (err := _validate_region(region)):
        return {"status": "validation_error", "error": err, "field": "region"}
    try:
        mid = UUID(merchant_id)
    except ValueError:
        return {
            "status": "validation_error",
            "error": f"merchant_id {merchant_id!r} is not a valid UUID",
            "field": "merchant_id",
        }
    result = await execute(
        "UPDATE agents.merchant_profiles "
        "SET region = $2, updated_at = NOW() "
        "WHERE merchant_id = $1",
        mid,
        region,
    )
    if not result.endswith(" 1"):
        return {
            "status": "validation_error",
            "error": f"merchant_id {merchant_id!r} not found",
            "field": "merchant_id",
        }
    return {"status": "ok", "merchant_id": merchant_id, "region": region}
