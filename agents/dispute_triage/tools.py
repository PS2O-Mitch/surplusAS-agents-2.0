"""ADK tools for the Dispute Triage agent.

Five tools (E1-E5 of the Phase 4 plan). Flow per master plan §4.5:
    fetch_recommendation_log(listing_id)  -> RecommendationLogEntry
    request_reprice(original_recommendation_id, partner_id)
                                          -> structured replay payload (lateral A2A -> Pricing)
    diff_pressures(old, new)              -> dict[str, float]
    persist_dispute(...)                  -> {"dispute_id": ...}
    emit_price_update_webhook(...)        -> delivery audit

This task (E1) adds only `fetch_recommendation_log`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from shared.db import fetch_one, init_pool


async def fetch_recommendation_log(*, listing_id: str) -> dict[str, Any]:
    """Return the most recent recommendation_log row for `listing_id`.

    Going through `recommendation_log` directly (ORDER BY created_at DESC
    LIMIT 1) avoids assuming `agents.listings.current_recommendation_id` is
    in sync. The Dispute Triage prompt treats this row's `recommended_price`
    + `applied_pressures` as the "old" side of the diff.
    """
    try:
        UUID(listing_id)
    except ValueError:
        return {"status": "validation_error",
                "error": f"listing_id {listing_id!r} is not a valid UUID",
                "field": "listing_id"}

    await init_pool()
    row = await fetch_one(
        "SELECT recommendation_id, listing_id, merchant_id, partner_id, "
        "       pricing_input, recommended_price, recommended_discount_pct, "
        "       anchor_p50, anchor_source, anchor_region, "
        "       applied_pressures, formula_version, coefficients_version, "
        "       replay_of "
        "FROM agents.recommendation_log "
        "WHERE listing_id = $1 "
        "ORDER BY created_at DESC LIMIT 1",
        listing_id,
    )
    if row is None:
        return {"status": "not_found",
                "error": f"no recommendation found for listing_id {listing_id!r}"}
    return {"status": "ok", "recommendation": dict(row)}


async def diff_pressures(*, old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Compute per-pressure delta (`new - old`).

    Numeric pressures: float delta. Boolean clamp flags: -1/0/+1 transition.
    Keys present in only one map carry the present value as the delta
    (with the missing side treated as 0 / False).

    Pure function — no DB, no A2A. Safe to call inside the model's
    reasoning loop without IO.

    async for ADK tool-surface consistency (see Phase 3 A2 review feedback).
    """
    keys = set(old) | set(new)
    deltas: dict[str, Any] = {}
    for k in keys:
        old_v = old.get(k, 0)
        new_v = new.get(k, 0)
        if isinstance(old_v, bool) or isinstance(new_v, bool):
            deltas[k] = int(bool(new_v)) - int(bool(old_v))
        else:
            deltas[k] = float(new_v) - float(old_v)
    return {"status": "ok", "deltas": deltas}
