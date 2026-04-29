"""Listing Intake — parses merchant draft (text + optional photo) into a ListingDraft.

After parsing, MUST call `request_anchor_price` (lateral A2A → Pricing) before
persisting. Listings are never saved without a recommendation attached. If
pricing returns no anchor, persist with status='draft_no_price' and surface
the gap to the Concierge.

TODO Week 3: parse_draft / validate_listing / request_anchor_price / persist_listing
tools, system prompt, ADK Agent.
"""

from __future__ import annotations

AGENT_NAME = "listing_intake"
