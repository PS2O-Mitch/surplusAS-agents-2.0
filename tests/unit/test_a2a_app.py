"""Unit tests for the standard A2A server surface (`service.a2a_app`).

Verifies each SurplusAS agent can be exposed over the OPEN Agent-to-Agent
(A2A) protocol via ADK's native `to_a2a()` adapter: a discoverable Agent Card
at the well-known URL plus a JSON-RPC endpoint. This is what satisfies the
Track-3 A2A interoperability mandate — framework-agnostic discovery + JSON-RPC,
NOT the proprietary Agent Engine `async_stream_query` transport.

The card + routes are registered in the Starlette lifespan startup hook, so
the tests enter the `TestClient` context manager (which runs lifespan) before
asserting.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from service.a2a_app import VALID_AGENTS, build_a2a_app

AGENT_CARD_PATH = "/.well-known/agent-card.json"


def test_build_a2a_app_serves_discoverable_agent_card() -> None:
    app = build_a2a_app("pricing", host="localhost", port=8080, protocol="http")
    with TestClient(app) as client:
        resp = client.get(AGENT_CARD_PATH)
        assert resp.status_code == 200
        card = resp.json()
        # Agent identity is discoverable.
        assert card["name"]
        # The JSON-RPC transport endpoint is advertised (open A2A, not async_stream_query).
        assert card["url"] == "http://localhost:8080"
        # Skills are declared so a peer enterprise agent knows what it can do.
        assert card["skills"]


def test_agent_card_advertises_public_url_from_params() -> None:
    app = build_a2a_app(
        "pricing", host="pricing.run.app", port=443, protocol="https",
    )
    with TestClient(app) as client:
        card = client.get(AGENT_CARD_PATH).json()
        assert card["url"] == "https://pricing.run.app:443"


def test_build_a2a_app_rejects_unknown_agent() -> None:
    with pytest.raises(ValueError, match="unknown agent"):
        build_a2a_app("not_an_agent")


def test_valid_agents_is_the_full_mesh() -> None:
    assert set(VALID_AGENTS) == {
        "concierge", "pricing", "onboarding", "listing_intake", "dispute_triage",
    }
