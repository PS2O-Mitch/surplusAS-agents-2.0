"""Concierge — single externally-addressable agent. Routes turns to specialists via A2A.

TODO Week 3: implement the four routing tools backed by `shared.a2a.call_peer_agent`,
wire system prompt from `prompts.py`, and instantiate the ADK Agent + Runner.
"""

from __future__ import annotations

AGENT_NAME = "concierge"
