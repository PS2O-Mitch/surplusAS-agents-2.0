"""Concierge — single externally-addressable agent. Routes turns to specialists via A2A.

The Concierge is the merchant's single point of contact. It never prices,
never moderates, never writes to the DB. Each turn picks exactly one of the
four routing tools and narrates the specialist's result back to the merchant,
surfacing `applied_pressures` verbatim when present.
"""

from __future__ import annotations

from google.adk import Agent

from shared.config import get_settings

from .prompts import SYSTEM_PROMPT
from .tools import (
    route_to_dispute_triage,
    route_to_listing_intake,
    route_to_onboarding,
    route_to_pricing,
)

AGENT_NAME = "concierge"

agent = Agent(
    name=AGENT_NAME,
    description=(
        "Concierge for SurplusAS. The only externally-addressable agent. "
        "Routes each merchant turn to one of four specialists "
        "(Onboarding, Listing Intake, Pricing, Dispute Triage) over A2A."
    ),
    model=get_settings().concierge_model,
    instruction=SYSTEM_PROMPT,
    tools=[
        route_to_onboarding,
        route_to_listing_intake,
        route_to_pricing,
        route_to_dispute_triage,
    ],
)
