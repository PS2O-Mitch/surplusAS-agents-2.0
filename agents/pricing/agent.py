"""Pricing agent — thin LLM shell over the deterministic pricing engine.

The model parses merchant intent and writes rationale; it never invents a
number. All numeric output flows through `engine_adapter.price_listing` /
`engine_adapter.replay_recommendation`, which are the only callers of
`pricing_engine.formula.recommend()` and the only writers to
`agents.recommendation_log` (CLAUDE.md guardrails #1 + #3).

The exported `agent` object is what `vertexai.agent_engines.create()`
wraps in an `AdkApp` for deployment to Agent Engine.
"""

from __future__ import annotations

from google.adk import Agent

from shared.config import get_settings

from .prompts import SYSTEM_PROMPT
from .tools import lookup_anchor_tool, price_listing, replay_recommendation

AGENT_NAME = "pricing"

agent = Agent(
    name=AGENT_NAME,
    description=(
        "Deterministic pricing specialist for SurplusAS. Resolves anchors and "
        "coefficients, runs the pure-Python pricing formula, appends a row to "
        "agents.recommendation_log, and surfaces applied_pressures verbatim."
    ),
    model=get_settings().pricing_model,
    instruction=SYSTEM_PROMPT,
    tools=[lookup_anchor_tool, price_listing, replay_recommendation],
)
