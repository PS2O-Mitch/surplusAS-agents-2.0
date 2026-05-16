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
    user_message = _compose_concierge_message(body)
    aggregated = await a2a.call_concierge(
        user_message=user_message,
        partner_id=DEMO_PARTNER_ID,
    )
    return {
        "narration": aggregated["narration"],
        "specialist_called": aggregated["specialist_called"],
        "specialist_payload": aggregated["specialist_payload"],
    }


def _compose_concierge_message(body: dict[str, Any]) -> str:
    """Pack merchant_id / listing_id context hints into the user message string.

    ADK's Runner accepts a string (auto-wrapped as user content) but not the
    `{mode, input}` envelope our internal A2A helper uses. We surface the
    optional context fields as a leading bracketed prefix so the Concierge's
    prompt can still see them, while keeping the message a plain string.
    Image attachments are TODO Phase 4 (need multimodal Content wrapping).
    """
    message = body.get("message", "")
    parts: list[str] = []
    if body.get("merchant_id"):
        parts.append(f"merchant_id={body['merchant_id']}")
    if body.get("listing_id"):
        parts.append(f"listing_id={body['listing_id']}")
    if parts:
        return f"[{', '.join(parts)}] {message}"
    return str(message)


@router.post("/listings/publish")
async def demo_publish_listing(body: dict[str, Any]) -> dict[str, Any]:
    """Force a publish through Listing Intake (used by the static UI's Save flow)."""
    return await a2a.call_peer_agent(
        peer="listing_intake",
        mode="publish",
        input=body,
        partner_id=DEMO_PARTNER_ID,
    )
