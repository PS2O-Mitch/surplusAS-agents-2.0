"""Onboarding — converts merchant freeform input into a `MerchantProfile`.

Owns the only writes to `agents.merchant_profiles`. Concierge dispatches
here on the first turn (cold-start) and any time the merchant amends their
profile mid-session.
"""

from __future__ import annotations

from google.adk import Agent

from shared.config import get_settings

from .prompts import SYSTEM_PROMPT
from .tools import (
    create_merchant_profile,
    set_categories,
    set_floor_pct,
    set_region,
)

AGENT_NAME = "onboarding"

agent = Agent(
    name=AGENT_NAME,
    description=(
        "Onboarding specialist for SurplusAS. Captures merchant profile fields "
        "(name, region, allowed_categories, floor pct) and writes them to "
        "agents.merchant_profiles."
    ),
    model=get_settings().onboarding_model,
    instruction=SYSTEM_PROMPT,
    tools=[create_merchant_profile, set_floor_pct, set_categories, set_region],
)
