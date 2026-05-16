"""Same-origin demo shims for the bundled static UI.

The static page (`service/static/surplusas-merchant-demo.html`) can't carry an
API key — it's just HTML served from the same origin. The demo shim runs
unauthenticated and forces `partner_id=sk_demo_surplus_2026` so the static
page works out of the box. In production the static path is gated by Cloud
Run IAP; the shim is not publicly exposed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from shared import a2a

router = APIRouter(prefix="/demo/v1", tags=["demo"])
DEMO_PARTNER_ID = "sk_demo_surplus_2026"


@router.post("/agent")
async def demo_agent(body: dict[str, Any]) -> dict[str, Any]:
    """Delegate to /v1/concierge with a fixed demo partner_id."""
    final = await a2a.call_peer_agent(
        peer="concierge",
        mode="process",
        input={
            "message": body.get("message", ""),
            "merchant_id": body.get("merchant_id"),
            "listing_id": body.get("listing_id"),
            "image_b64": body.get("image"),
        },
        partner_id=DEMO_PARTNER_ID,
    )
    return {
        "narration": final.get("narration", ""),
        "specialist_called": final.get("specialist_called"),
        "specialist_payload": final.get("specialist_payload", {}),
    }


@router.post("/listings/publish")
async def demo_publish_listing(body: dict[str, Any]) -> dict[str, Any]:
    """Force a publish through Listing Intake (used by the static UI's Save flow)."""
    return await a2a.call_peer_agent(
        peer="listing_intake",
        mode="publish",
        input=body,
        partner_id=DEMO_PARTNER_ID,
    )
