"""Public REST surface — Cloud Run gateway -> Concierge.

All routes here require `Authorization: Bearer <api_key>` resolved against
`public.partner_keys` via `shared.auth.require_partner`. The resolved
`PartnerContext` is passed into each handler as `partner` so the route can
authorize the body's `partner_id` claim and tag downstream calls.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status

from shared import a2a
from shared.auth import PartnerContext, PartnerDep
from shared.db import fetch_one, init_pool
from shared.schemas import ConciergeRequest, ConciergeResponse

router = APIRouter(prefix="/v1", tags=["public"])


@router.post("/concierge", response_model=ConciergeResponse)
async def post_concierge(
    body: ConciergeRequest,
    partner: PartnerContext = PartnerDep,
) -> ConciergeResponse:
    if body.partner_id != partner.partner_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="body.partner_id does not match authenticated identity",
        )
    final = await a2a.call_peer_agent(
        peer="concierge",
        mode="process",
        input={
            "message": body.message,
            "merchant_id": body.merchant_id,
            "listing_id": body.listing_id,
            "image_b64": body.image,
        },
        partner_id=partner.partner_id,
    )
    return ConciergeResponse(
        narration=final.get("narration", ""),
        specialist_called=final.get("specialist_called"),
        specialist_payload=final.get("specialist_payload", {}),
        trace_id=final.get("trace_id"),
    )


@router.get("/listings/{listing_id}")
async def get_listing(
    listing_id: str,
    partner: PartnerContext = PartnerDep,
) -> dict[str, Any]:
    """Return a listing joined with its current recommendation."""
    await init_pool()
    row = await fetch_one(
        "SELECT l.listing_id, l.title, l.description, l.category, l.units, "
        "       l.retail_value, l.hours_until_expiry, l.image_uri, l.status, "
        "       r.recommended_price, r.recommended_discount_pct, "
        "       r.applied_pressures, r.formula_version, r.coefficients_version "
        "FROM agents.listings l "
        "JOIN agents.recommendation_log r "
        "  ON r.recommendation_id = l.current_recommendation_id "
        "WHERE l.listing_id = $1 AND l.partner_id = $2",
        listing_id,
        partner.partner_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="listing not found")
    return dict(row)
