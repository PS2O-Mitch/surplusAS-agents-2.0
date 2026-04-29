"""Pricing agent — thin LLM shell over the deterministic pricing engine.

The model parses merchant intent and writes rationale; it never invents a number.
All numeric output flows through `engine_adapter.price_listing()` /
`engine_adapter.replay_recommendation()`, which is the only path that calls
`pricing_engine.formula.recommend()` and the only writer to
`agents.recommendation_log`.

TODO Week 2: implement engine_adapter, three tools, system prompt, ADK Agent.
"""

from __future__ import annotations

AGENT_NAME = "pricing"
