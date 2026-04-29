"""Dispute Triage — replays prior recommendations and narrates pressure deltas.

On dispute: (1) fetch original recommendation_log row by listing_id;
(2) call Pricing replay (lateral A2A); (3) compute per-pressure delta;
(4) write disputes row; (5) emit price.updated webhook if delta > $0.25;
(6) narrate which pressures moved.

TODO Week 4: fetch_recommendation_log / request_reprice / diff_pressures /
persist_dispute / emit_price_update_webhook tools; system prompt; ADK Agent.
"""

from __future__ import annotations

AGENT_NAME = "dispute_triage"
