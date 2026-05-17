"""REST surface for disputes.

POST /v1/listings/{listing_id}/dispute — direct dispute submission, bypasses
the Concierge chat flow. The route verifies the listing belongs to the
authenticated partner, then sends a freeform dispute message to the
Dispute Triage agent via the same aggregator the Concierge uses.

GET /v1/disputes/{dispute_id} — fetch a full Dispute row (Phase 4 read-only;
resolution lifecycle endpoints are Phase 5).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from shared import a2a
from shared.auth import PartnerContext, PartnerDep
from shared.db import fetch_one, init_pool

router = APIRouter(prefix="/v1", tags=["disputes"])


@router.post("/listings/{listing_id}/dispute")
async def post_dispute(
    listing_id: str,
    body: dict[str, Any],
    partner: PartnerContext = PartnerDep,
) -> dict[str, Any]:
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=422, detail="reason is required")

    await init_pool()
    row = await fetch_one(
        "SELECT listing_id, merchant_id, partner_id "
        "FROM agents.listings WHERE listing_id = $1 AND partner_id = $2",
        listing_id, partner.partner_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="listing not found")

    user_message = (
        f"Dispute on listing_id={listing_id} merchant_id={row['merchant_id']}: "
        f"{reason}"
    )
    agg = await a2a.aggregate_peer_stream(
        "dispute_triage", user_message, partner.partner_id,
    )
    return {
        "listing_id": listing_id,
        "narration": agg["narration"],
        "event_count": agg["event_count"],
    }


@router.get("/disputes/{dispute_id}")
async def get_dispute(
    dispute_id: str,
    partner: PartnerContext = PartnerDep,
) -> dict[str, Any]:
    await init_pool()
    row = await fetch_one(
        "SELECT dispute_id, listing_id, merchant_id, partner_id, reason_text, "
        "       original_recommendation_id, new_recommendation_id, "
        "       pressure_diff, resolution, created_at, resolved_at "
        "FROM agents.disputes "
        "WHERE dispute_id = $1 AND partner_id = $2",
        dispute_id, partner.partner_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="dispute not found")
    return dict(row)
