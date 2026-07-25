"""Same-origin demo shims for the bundled static UI.

The static page (`service/static/surplusas-merchant-demo.html`) can't carry an
API key — it's just HTML served from the same origin. The demo shim runs
unauthenticated and forces `partner_id=demo_001` — the partner the committed
demo key (`sk_demo_surplus_2026`) resolves to through `public.partner_keys`,
so shim-created rows are visible to the authenticated `/v1/*` surface. The
whole surface (shim + static mount) is only registered when `DEMO_MODE=true`
(`service/app.py`) — never in production.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from fastapi import APIRouter

from shared import a2a
from shared.db import fetch_one, init_pool

router = APIRouter(prefix="/demo/v1", tags=["demo"])
DEMO_PARTNER_ID = "demo_001"

_demo_merchant_cache: str | None = None


async def _demo_merchant_id() -> str | None:
    """The demo partner's first merchant profile, cached for the process.

    The static page never sends a merchant_id, but Listing Intake resolves
    region/floor/local-hour from the merchant profile — without one every
    demo listing dead-ends in an onboarding prompt. Errors resolve to None
    so the shim never 500s here; the agent just asks the merchant to
    onboard first.
    """
    global _demo_merchant_cache
    if _demo_merchant_cache is None:
        with suppress(Exception):
            await init_pool()
            row = await fetch_one(
                "SELECT merchant_id FROM agents.merchant_profiles "
                "WHERE partner_id = $1 ORDER BY created_at LIMIT 1",
                DEMO_PARTNER_ID,
            )
            if row is not None:
                _demo_merchant_cache = str(row["merchant_id"])
    return _demo_merchant_cache


@router.post("/agent")
async def demo_agent(body: dict[str, Any]) -> dict[str, Any]:
    """Delegate to /v1/concierge with a fixed demo partner_id."""
    if not body.get("merchant_id"):
        body["merchant_id"] = await _demo_merchant_id()
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
    """Extract the merchant's text from either the new or legacy envelope.

    The current REST contract uses `{message, merchant_id?, listing_id?, image?}`.
    The bundled static demo (`service/static/surplusas-merchant-demo.html`) is
    a port from SurplusAS-API-2.0 and still sends the legacy
    `{mode, input, image?}` envelope — see line 863 of the page. Accept both
    shapes here so the page works without a rewrite.

    ADK's Runner accepts a string (auto-wrapped as user content) but not a
    dict envelope, so we materialise the actual merchant text into a string.
    Context fields surface as a leading bracketed prefix.
    """
    message = body.get("message") or body.get("input") or ""
    # partner_id always injected — see routes_rest._compose_concierge_message.
    parts: list[str] = [f"partner_id={DEMO_PARTNER_ID}"]
    if body.get("merchant_id"):
        parts.append(f"merchant_id={body['merchant_id']}")
    if body.get("listing_id"):
        parts.append(f"listing_id={body['listing_id']}")
    return f"[{', '.join(parts)}] {message}"


@router.post("/listings/publish")
async def demo_publish_listing(body: dict[str, Any]) -> dict[str, Any]:
    """Force a publish through Listing Intake (used by the static UI's Save flow)."""
    if not body.get("merchant_id"):
        body["merchant_id"] = await _demo_merchant_id()
    return await a2a.call_peer_agent(
        peer="listing_intake",
        mode="publish",
        input=body,
        partner_id=DEMO_PARTNER_ID,
    )


@router.post("/listings/{listing_id}/dispute")
async def demo_open_dispute(
    listing_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Demo shim: open a dispute on an existing listing.

    Mirrors `/v1/listings/{listing_id}/dispute` but forces the demo
    partner so the bundled static UI can drive Beat 2 without an API
    key. Routes through Dispute Triage via aggregate_peer_stream so we
    return the human-readable narration the demo UI renders.
    """
    reason = (body.get("reason") or "").strip()
    if not reason:
        return {"error": "reason is required"}
    user_message = (
        f"Dispute on listing_id={listing_id}: {reason}"
    )
    agg = await a2a.aggregate_peer_stream(
        "dispute_triage", user_message, DEMO_PARTNER_ID,
    )
    return {
        "listing_id": listing_id,
        "narration": agg["narration"],
        "tool_calls": agg["tool_calls"],
        "event_count": agg["event_count"],
    }
