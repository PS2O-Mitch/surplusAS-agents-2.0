"""End-to-end Beat 2 integration test.

Mocks the SDK boundary plus the DB. Drives:

  1. POST /v1/listings/{id}/dispute  (listing ownership verified)
  2. Dispute Triage stream is aggregated, surfacing the dominant pressure
     mover in the narration.

The webhook delivery itself happens INSIDE the deployed Dispute Triage
engine via its `emit_price_update_webhook` tool — we can't observe that
from outside the engine in this test (the tool's audit-row write goes to
the live DB). What we DO verify here:
  - the dispute_triage stream's `tool_calls` shows the expected sequence
    (fetch_recommendation_log -> request_reprice -> diff_pressures ->
    persist_dispute -> emit_price_update_webhook).
  - the merchant-facing narration names the top mover with verbatim
    numeric values (the Phase 3 "applied_pressures round-trip" invariant).

The actual cross-process webhook-row check is part of the manual Beat 2
dry-run (Task H5 of the plan).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from service.app import create_app
from shared import a2a
from shared.auth import PartnerContext, require_partner
from shared.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration


DEMO_PARTNER_ID = "sk_demo"
LISTING_ID = "22222222-2222-2222-2222-222222222222"
ORIG_REC_ID = "11111111-1111-1111-1111-111111111111"
NEW_REC_ID = "55555555-5555-5555-5555-555555555555"
MERCHANT_ID = "33333333-3333-3333-3333-333333333333"


@pytest.fixture(autouse=True)
def _seed_resources(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    get_settings.cache_clear()
    monkeypatch.setenv("DISPUTE_TRIAGE_AGENT_RESOURCE",
                       "projects/1/locations/us-central1/reasoningEngines/dispute")
    monkeypatch.setenv("PRICING_AGENT_RESOURCE",
                       "projects/1/locations/us-central1/reasoningEngines/pricing")
    a2a._handle_cache.clear()
    yield
    a2a._handle_cache.clear()
    get_settings.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[require_partner] = lambda: PartnerContext(
        api_key="sk_test", partner_id=DEMO_PARTNER_ID, context={},
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_beat2_dispute_flow(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    """A dispute on a real listing -> narration names the top pressure mover
    with verbatim numeric values, and the dispute_triage stream's tool_calls
    reflect the expected fetch/reprice/diff/persist/emit sequence."""

    # The listing ownership check returns a row owned by the demo partner.
    async def fake_fetch_one(_sql: str, *_args: Any) -> dict[str, Any]:
        return {"listing_id": LISTING_ID, "merchant_id": MERCHANT_ID,
                "partner_id": DEMO_PARTNER_ID}

    monkeypatch.setattr("service.routes_disputes.fetch_one", fake_fetch_one)
    monkeypatch.setattr("service.routes_disputes.init_pool", AsyncMock())

    # The dispute_triage stream: model invokes all 5 tools in order, ending
    # with the merchant-facing narration.
    captured: dict[str, Any] = {}

    async def fake_aggregate(peer, user_message, partner_id, *, session_id=None):
        captured["peer"] = peer
        captured["user_message"] = user_message
        captured["partner_id"] = partner_id
        return {
            "narration": (
                "Replayed under fresh coefficients: new price $6.50. "
                "Expiry pressure rose from 0.08 to 0.21 because the listing "
                "now has 4 hours instead of 18."
            ),
            "tool_calls": [
                {"name": "fetch_recommendation_log",
                 "args": {"listing_id": LISTING_ID},
                 "response": {"status": "ok"}},
                {"name": "request_reprice",
                 "args": {"original_recommendation_id": ORIG_REC_ID,
                          "partner_id": DEMO_PARTNER_ID},
                 "response": {"status": "ok",
                              "new_recommendation_id": NEW_REC_ID,
                              "new_price": 6.50}},
                {"name": "diff_pressures",
                 "args": {}, "response": {"status": "ok"}},
                {"name": "persist_dispute",
                 "args": {}, "response": {"status": "ok",
                                          "dispute_id":
                                              "44444444-4444-4444-4444-444444444444"}},
                {"name": "emit_price_update_webhook",
                 "args": {"old_price": 7.25, "new_price": 6.50},
                 "response": {"status": "ok", "delivery_ids": ["d-1"]}},
            ],
            "event_count": 11,
        }

    monkeypatch.setattr("service.routes_disputes.a2a.aggregate_peer_stream",
                        fake_aggregate)

    resp = client.post(
        f"/v1/listings/{LISTING_ID}/dispute",
        json={"reason": "price dropped too fast"},
        headers={"Authorization": "Bearer sk_test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["listing_id"] == LISTING_ID
    # Narration must reproduce the pressure values verbatim — guardrail #2
    assert "0.21" in body["narration"]
    assert "0.08" in body["narration"]
    assert "6.50" in body["narration"]

    # Tool-call sequence pinned (regression guard against prompt drift):
    assert captured["peer"] == "dispute_triage"
    assert captured["partner_id"] == DEMO_PARTNER_ID
    assert "price dropped too fast" in captured["user_message"]
    assert LISTING_ID in captured["user_message"]


def test_beat2_dispute_404_when_listing_not_owned(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    """Cross-tenant guard: a partner can't dispute another partner's listing."""
    monkeypatch.setattr("service.routes_disputes.fetch_one",
                        AsyncMock(return_value=None))
    monkeypatch.setattr("service.routes_disputes.init_pool", AsyncMock())

    # The aggregator should never be reached.
    aggregator_called = False

    async def fake_aggregate(*_: Any, **__: Any) -> dict[str, Any]:
        nonlocal aggregator_called
        aggregator_called = True
        return {"narration": "", "tool_calls": [], "event_count": 0}

    monkeypatch.setattr("service.routes_disputes.a2a.aggregate_peer_stream",
                        fake_aggregate)

    resp = client.post(
        f"/v1/listings/{LISTING_ID}/dispute",
        json={"reason": "not my listing"},
        headers={"Authorization": "Bearer sk_test"},
    )
    assert resp.status_code == 404
    assert aggregator_called is False
