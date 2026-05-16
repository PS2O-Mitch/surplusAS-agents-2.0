"""Concierge routing tools.

Each tool is a thin wrapper over `shared.a2a.call_peer_agent`. The Concierge
agent's prompt decides which one to invoke per turn; the tool body just packs
the A2A message envelope (mode + input + partner_id) and returns the final
stream event verbatim. Narration of `applied_pressures` is the model's job,
not the tool's.
"""

from __future__ import annotations

from typing import Any

from shared import a2a


async def route_to_onboarding(
    *,
    message: str,
    partner_id: str,
    merchant_id: str | None = None,
) -> dict[str, Any]:
    """Hand the merchant's turn to Onboarding (profile setup / amendments)."""
    return await a2a.call_peer_agent(
        peer="onboarding",
        mode="process",
        input={"message": message, "merchant_id": merchant_id},
        partner_id=partner_id,
    )


async def route_to_listing_intake(
    *,
    message: str,
    partner_id: str,
    merchant_id: str | None = None,
    image_b64: str | None = None,
) -> dict[str, Any]:
    """Hand the merchant's turn to Listing Intake (draft -> priced listing)."""
    return await a2a.call_peer_agent(
        peer="listing_intake",
        mode="process",
        input={"message": message, "merchant_id": merchant_id, "image_b64": image_b64},
        partner_id=partner_id,
    )


async def route_to_pricing(
    *,
    pricing_input_json: dict[str, Any],
    partner_id: str,
) -> dict[str, Any]:
    """Ask Pricing for a one-off recommendation (no listing attached yet)."""
    return await a2a.call_peer_agent(
        peer="pricing",
        mode="price_listing",
        input=pricing_input_json,
        partner_id=partner_id,
    )


async def route_to_dispute_triage(
    *,
    listing_id: str,
    dispute_text: str,
    partner_id: str,
) -> dict[str, Any]:
    """Forward a dispute to Dispute Triage (replay + per-pressure diff)."""
    return await a2a.call_peer_agent(
        peer="dispute_triage",
        mode="resolve",
        input={"listing_id": listing_id, "reason": dispute_text},
        partner_id=partner_id,
    )
