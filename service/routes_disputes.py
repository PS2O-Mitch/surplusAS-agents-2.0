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
from shared.db import execute, fetch_one, init_pool
from shared.webhook_events import emit_event

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


_VALID_RESOLUTIONS = {"accepted", "rejected", "withdrawn"}


@router.patch("/disputes/{dispute_id}")
async def patch_dispute(
    dispute_id: str,
    body: dict[str, Any],
    partner: PartnerContext = PartnerDep,
) -> dict[str, Any]:
    """Resolve a dispute (pending -> accepted/rejected/withdrawn).

    Resolutions are append-only: once a dispute is non-pending, PATCH
    returns 409. The route emits `dispute.resolved` after a successful
    UPDATE — webhook failures don't fail the resolution (the audit row
    is the source of truth, mirrors the in-agent pattern in K1/K2).
    """
    resolution = body.get("resolution")
    if resolution not in _VALID_RESOLUTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"resolution must be one of {sorted(_VALID_RESOLUTIONS)}",
        )

    await init_pool()
    row = await fetch_one(
        "SELECT dispute_id, listing_id, merchant_id, partner_id, resolution "
        "FROM agents.disputes WHERE dispute_id = $1 AND partner_id = $2",
        dispute_id, partner.partner_id,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="dispute not found")
    if row["resolution"] != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"dispute already resolved as {row['resolution']!r}",
        )

    await execute(
        "UPDATE agents.disputes "
        "SET resolution = $2, resolved_at = NOW() "
        "WHERE dispute_id = $1",
        dispute_id, resolution,
    )

    webhook_status: str
    webhook_delivery_ids: list[str] = []
    webhook_error: str | None = None
    try:
        emit_result = await emit_event(
            event_type="dispute.resolved",
            partner_id=partner.partner_id,
            payload={
                "dispute_id": dispute_id,
                "listing_id": str(row["listing_id"]),
                "merchant_id": str(row["merchant_id"]),
                "resolution": resolution,
            },
        )
        webhook_status = emit_result.get("status", "unknown")
        webhook_delivery_ids = emit_result.get("delivery_ids", [])
    except Exception as exc:  # noqa: BLE001 — webhook failure is non-fatal
        webhook_status = "error"
        webhook_error = str(exc)[:500]

    response: dict[str, Any] = {
        "dispute_id": dispute_id,
        "resolution": resolution,
        "webhook_status": webhook_status,
        "webhook_delivery_ids": webhook_delivery_ids,
    }
    if webhook_error is not None:
        response["webhook_error"] = webhook_error
    return response
