"""ADK FunctionTools for the Pricing agent.

These are thin LLM-friendly wrappers over `engine_adapter.py`. The model
sees a flat keyword-argument signature and JSON-serialisable return values;
the tool body re-hydrates the Pydantic types and forwards to the adapter.

Three tools, mirroring the canonical plan §4.2:

- `lookup_anchor_tool` — read-only anchor probe (no write to recommendation_log).
- `price_listing` — full path: anchor + coefficients + formula → log row.
- `replay_recommendation` — re-derive from a prior row, write a new log row.

Returning a plain dict (not a Pydantic model) is intentional. ADK 2.0's
auto-tool extractor produces a JSON-Schema for the model from the function
signature, and JSON-Schema-friendly returns keep the parsed tool result
predictable on the model side.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from shared.db import init_pool
from shared.pricing_intel import PricingInput, lookup_anchor

from .engine_adapter import (
    NoAnchorError,
    NoCoefficientsError,
    RecommendationNotFoundError,
)
from .engine_adapter import (
    price_listing as _adapter_price_listing,
)
from .engine_adapter import (
    replay_recommendation as _adapter_replay_recommendation,
)


async def lookup_anchor_tool(
    category: str,
    region: str,
    tier: str | None = None,
) -> dict[str, Any]:
    """Look up the best anchor for (category, region) without writing a log row.

    Useful when the merchant asks "what would this price at?" before they
    commit to a draft. Returns `{"status": "no_anchor"}` when no row exists.
    """
    # Lazy init: asyncpg pools are bound to the event loop that creates them,
    # so the deployed agent can't pre-init in AdkApp.set_up() (different loop).
    # init_pool() is idempotent (lock-guarded) — safe to call on every tool entry.
    pool = await init_pool()
    async with pool.acquire() as conn:
        anchor = await lookup_anchor(
            conn, "postgres", category=category, region=region, tier=tier
        )
    if anchor is None:
        return {"status": "no_anchor", "category": category, "region": region}
    return {
        "status": "ok",
        "anchor": {
            "p25": anchor.p25,
            "p50": anchor.p50,
            "p75": anchor.p75,
            "source": anchor.source,
            "region": anchor.region,
            "tier": anchor.tier,
            "sample_count": anchor.sample_count,
            "wholesale_markup_applied": anchor.wholesale_markup_applied,
        },
    }


async def price_listing(
    *,
    category: str,
    region: str,
    units: int,
    retail_value: float,
    hours_until_expiry: float,
    now_hour: int,
    merchant_floor_pct: float,
    partner_id: str,
    listing_id: str | None = None,
    merchant_id: str | None = None,
) -> dict[str, Any]:
    """Compute a price and append a row to `agents.recommendation_log`.

    All numeric work happens in the deterministic engine. Returns a dict
    shaped like the prompt's `recommendation` envelope so the agent can
    forward it verbatim. On no_anchor returns `{"status": "no_anchor"}`.
    """
    try:
        pricing_input = PricingInput(
            category=category,
            region=region,
            units=units,
            retail_value=retail_value,
            hours_until_expiry=hours_until_expiry,
            now_hour=now_hour,
            merchant_floor_pct=merchant_floor_pct,
        )
    except ValueError as exc:
        return {"status": "validation_error", "error": str(exc)}

    try:
        entry = await _adapter_price_listing(
            pricing_input,
            partner_id,
            listing_id=UUID(listing_id) if listing_id else None,
            merchant_id=UUID(merchant_id) if merchant_id else None,
        )
    except NoAnchorError as exc:
        return {"status": "no_anchor", "error": str(exc)}
    except NoCoefficientsError as exc:
        return {"status": "validation_error", "error": str(exc)}

    return {"status": "ok", "recommendation": entry.model_dump(mode="json")}


async def replay_recommendation(recommendation_id: str) -> dict[str, Any]:
    """Re-derive a price under fresh coefficients/anchor; write a new log row.

    Used by Dispute Triage: original `recommendation_id` produced too low /
    too high a price; rerunning with current coefficients usually shifts
    the result. The new row's `replay_of` field links it back to the
    original for the disputes audit trail.
    """
    try:
        entry = await _adapter_replay_recommendation(UUID(recommendation_id))
    except RecommendationNotFoundError as exc:
        return {"status": "validation_error", "error": str(exc)}
    except NoAnchorError as exc:
        return {"status": "no_anchor", "error": str(exc)}
    except NoCoefficientsError as exc:
        return {"status": "validation_error", "error": str(exc)}

    return {"status": "ok", "recommendation": entry.model_dump(mode="json")}
