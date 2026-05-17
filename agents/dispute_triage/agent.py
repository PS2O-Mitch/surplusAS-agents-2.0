"""Dispute Triage — replays prior recommendations and narrates pressure deltas.

On dispute: (1) fetch original recommendation_log row by listing_id;
(2) call Pricing replay (lateral A2A); (3) compute per-pressure delta;
(4) write disputes row; (5) emit price.updated webhook if delta > $0.25;
(6) narrate which pressures moved.
"""

from __future__ import annotations

from google.adk import Agent

from shared.config import get_settings

from .prompts import SYSTEM_PROMPT
from .tools import (
    diff_pressures,
    emit_price_update_webhook,
    fetch_recommendation_log,
    persist_dispute,
    request_reprice,
)

AGENT_NAME = "dispute_triage"

agent = Agent(
    name=AGENT_NAME,
    description=(
        "Dispute Triage specialist for SurplusAS. Re-derives a listing's "
        "price under fresh coefficients, persists the dispute, narrates "
        "per-pressure deltas, and emits price.updated webhooks when the "
        "price moves by more than $0.25."
    ),
    model=get_settings().dispute_triage_model,
    instruction=SYSTEM_PROMPT,
    tools=[fetch_recommendation_log, request_reprice, diff_pressures,
           persist_dispute, emit_price_update_webhook],
)
