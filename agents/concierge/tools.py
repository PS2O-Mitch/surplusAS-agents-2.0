"""Concierge routing tools.

Each tool wraps `shared.a2a.aggregate_peer_stream`, which sends the message
to the chosen specialist as a plain string (ADK Runner's expected shape)
and walks the entire response stream. The tool returns a **structured
dict** the Concierge model can quote verbatim — narration + status — so
the Concierge prompt doesn't have to reason over raw ADK event shapes.
"""

from __future__ import annotations

from typing import Any

from shared import a2a


def _compose_specialist_message(
    message: str,
    *,
    merchant_id: str | None = None,
    listing_id: str | None = None,
    image_b64: str | None = None,
) -> str:
    """Pack optional context fields as a leading bracketed prefix.

    Image attachments are deferred to Phase 4 (need multimodal Content
    wrapping); we surface only the fact that an image was present so the
    specialist's prompt can ask for it explicitly.
    """
    parts: list[str] = []
    if merchant_id:
        parts.append(f"merchant_id={merchant_id}")
    if listing_id:
        parts.append(f"listing_id={listing_id}")
    if image_b64:
        parts.append("image_attached=true")
    if parts:
        return f"[{', '.join(parts)}] {message}"
    return message


async def route_to_onboarding(
    *,
    message: str,
    partner_id: str,
    merchant_id: str | None = None,
) -> dict[str, Any]:
    """Hand the merchant's turn to Onboarding (profile setup / amendments)."""
    user_message = _compose_specialist_message(message, merchant_id=merchant_id)
    agg = await a2a.aggregate_peer_stream("onboarding", user_message, partner_id)
    return {"status": "ok", "narration": agg["narration"]}


async def route_to_listing_intake(
    *,
    message: str,
    partner_id: str,
    merchant_id: str | None = None,
    image_b64: str | None = None,
) -> dict[str, Any]:
    """Hand the merchant's turn to Listing Intake (draft -> priced listing)."""
    user_message = _compose_specialist_message(
        message, merchant_id=merchant_id, image_b64=image_b64,
    )
    agg = await a2a.aggregate_peer_stream("listing_intake", user_message, partner_id)
    return {"status": "ok", "narration": agg["narration"]}


async def route_to_pricing(
    *,
    pricing_input_json: dict[str, Any],
    partner_id: str,
) -> dict[str, Any]:
    """Ask Pricing for a one-off recommendation (no listing attached yet).

    Pricing's input is a structured dict, not freeform text, so we serialise
    it into a sentence the Pricing prompt knows how to parse (`price_listing`
    tool: keyword args spelled out in plain English).
    """
    pi = pricing_input_json
    user_message = (
        "Please price this listing using the price_listing tool. "
        f"Category: {pi.get('category')}. Region: {pi.get('region')}. "
        f"Units: {pi.get('units')}. Retail value: ${pi.get('retail_value')}. "
        f"Hours until expiry: {pi.get('hours_until_expiry')}. "
        f"Current hour (24h): {pi.get('now_hour')}. "
        f"Merchant floor pct: {pi.get('merchant_floor_pct')}. "
        f"Partner id: {partner_id}."
    )
    agg = await a2a.aggregate_peer_stream("pricing", user_message, partner_id)
    return {"status": "ok", "narration": agg["narration"]}


async def route_to_dispute_triage(
    *,
    listing_id: str,
    dispute_text: str,
    partner_id: str,
) -> dict[str, Any]:
    """Forward a dispute to Dispute Triage (replay + per-pressure diff)."""
    user_message = _compose_specialist_message(
        f"Dispute on listing_id={listing_id}: {dispute_text}",
        listing_id=listing_id,
    )
    agg = await a2a.aggregate_peer_stream("dispute_triage", user_message, partner_id)
    return {"status": "ok", "narration": agg["narration"]}
