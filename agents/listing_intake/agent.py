"""Listing Intake — turns merchant drafts into priced listings.

The agent parses freeform text (and optional photo) into a `ListingDraft`,
validates it, calls Pricing over the lateral A2A edge for a live anchor,
and persists the listing row bound to the new recommendation. Listings
are never saved without a recommendation (real or `draft_no_price`).
"""

from __future__ import annotations

from google.adk import Agent

from shared.config import get_settings

from .prompts import SYSTEM_PROMPT
from .tools import (
    parse_draft,
    persist_listing,
    request_anchor_price,
    validate_listing,
)

AGENT_NAME = "listing_intake"

agent = Agent(
    name=AGENT_NAME,
    description=(
        "Listing intake specialist for SurplusAS. Parses merchant drafts, "
        "validates against the category list, calls Pricing for a live anchor, "
        "and persists the listing bound to the new recommendation row."
    ),
    model=get_settings().listing_intake_model,
    instruction=SYSTEM_PROMPT,
    tools=[parse_draft, validate_listing, request_anchor_price, persist_listing],
)
