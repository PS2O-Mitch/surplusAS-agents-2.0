"""REST surface for webhook subscription lifecycle.

Phase 4 endpoints: POST (create) and DELETE (deactivate). Listing all
subscriptions, rotating secrets, and resolution-lifecycle endpoints are
Phase 5.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Response

from shared.auth import PartnerContext, PartnerDep
from shared.webhook_subscriptions import (
    create_subscription,
    deactivate_subscription,
)

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


@router.post("/subscriptions")
async def post_subscription(
    body: dict[str, Any],
    partner: PartnerContext = PartnerDep,
) -> dict[str, Any]:
    """Create a new active webhook subscription scoped to the caller's partner_id."""
    result = await create_subscription(
        partner_id=partner.partner_id,
        url=body.get("url", ""),
        events=body.get("events", []),
        secret=body.get("secret", ""),
    )
    if result.get("status") == "validation_error":
        raise HTTPException(status_code=422, detail=result["error"])
    return {"subscription_id": result["subscription_id"]}


@router.delete("/subscriptions/{subscription_id}", status_code=204)
async def delete_subscription(
    subscription_id: str,
    partner: PartnerContext = PartnerDep,
) -> Response:
    """Soft-delete (active=FALSE). Scoped by partner_id to prevent cross-tenant ops."""
    result = await deactivate_subscription(
        subscription_id=subscription_id,
        partner_id=partner.partner_id,
    )
    if result.get("status") == "validation_error":
        raise HTTPException(status_code=422, detail=result["error"])
    return Response(status_code=204)
