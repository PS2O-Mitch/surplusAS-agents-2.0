"""Unit tests for service.routes_disputes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from service.app import create_app
from shared.auth import PartnerContext, require_partner

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[require_partner] = lambda: PartnerContext(
        api_key="sk_test", partner_id="sk_demo", context={},
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_post_dispute_routes_to_dispute_triage(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    """Verifies the listing's partner ownership then invokes Dispute Triage
    via the same aggregator the Concierge uses."""
    listing_row = {
        "listing_id": "22222222-2222-2222-2222-222222222222",
        "merchant_id": "33333333-3333-3333-3333-333333333333",
        "partner_id": "sk_demo",
    }

    async def fake_fetch_one(_sql: str, *_args: Any) -> dict[str, Any]:
        return listing_row

    captured: dict[str, Any] = {}

    async def fake_aggregate(peer, user_message, partner_id, *, session_id=None):
        captured["peer"] = peer
        captured["user_message"] = user_message
        captured["partner_id"] = partner_id
        return {
            "narration": "Replayed under fresh coefficients: new price $6.50. "
                         "Expiry pressure rose from 0.08 to 0.21.",
            "tool_calls": [], "event_count": 5,
        }

    monkeypatch.setattr("service.routes_disputes.fetch_one", fake_fetch_one)
    monkeypatch.setattr("service.routes_disputes.init_pool", AsyncMock())
    monkeypatch.setattr("service.routes_disputes.a2a.aggregate_peer_stream",
                        fake_aggregate)

    resp = client.post(
        "/v1/listings/22222222-2222-2222-2222-222222222222/dispute",
        json={"reason": "price dropped too fast"},
        headers={"Authorization": "Bearer sk_test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "Replayed under fresh coefficients" in body["narration"]
    assert captured["peer"] == "dispute_triage"
    assert "22222222-2222-2222-2222-222222222222" in captured["user_message"]
    assert "price dropped too fast" in captured["user_message"]
    assert captured["partner_id"] == "sk_demo"


def test_post_dispute_404_when_listing_not_found(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    monkeypatch.setattr("service.routes_disputes.fetch_one",
                        AsyncMock(return_value=None))
    monkeypatch.setattr("service.routes_disputes.init_pool", AsyncMock())

    resp = client.post(
        "/v1/listings/22222222-2222-2222-2222-222222222222/dispute",
        json={"reason": "x"},
        headers={"Authorization": "Bearer sk_test"},
    )
    assert resp.status_code == 404


def test_post_dispute_422_when_reason_empty(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    """Empty / whitespace-only reason rejected at the route layer."""
    resp = client.post(
        "/v1/listings/22222222-2222-2222-2222-222222222222/dispute",
        json={"reason": "   "},
        headers={"Authorization": "Bearer sk_test"},
    )
    assert resp.status_code == 422


def test_get_dispute_returns_full_row(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    async def fake_fetch_one(_sql: str, *_args: Any) -> dict[str, Any]:
        return {
            "dispute_id": "44444444-4444-4444-4444-444444444444",
            "listing_id": "22222222-2222-2222-2222-222222222222",
            "merchant_id": "33333333-3333-3333-3333-333333333333",
            "partner_id": "sk_demo",
            "reason_text": "price dropped",
            "original_recommendation_id":
                "11111111-1111-1111-1111-111111111111",
            "new_recommendation_id":
                "55555555-5555-5555-5555-555555555555",
            "pressure_diff": {"expiry": 0.13},
            "resolution": "pending",
            "created_at": "2026-05-17T00:00:00Z",
            "resolved_at": None,
        }

    monkeypatch.setattr("service.routes_disputes.fetch_one", fake_fetch_one)
    monkeypatch.setattr("service.routes_disputes.init_pool", AsyncMock())

    resp = client.get(
        "/v1/disputes/44444444-4444-4444-4444-444444444444",
        headers={"Authorization": "Bearer sk_test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dispute_id"] == "44444444-4444-4444-4444-444444444444"
    assert body["resolution"] == "pending"
    assert body["pressure_diff"]["expiry"] == 0.13


def test_get_dispute_404_when_not_owned(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    monkeypatch.setattr("service.routes_disputes.fetch_one",
                        AsyncMock(return_value=None))
    monkeypatch.setattr("service.routes_disputes.init_pool", AsyncMock())

    resp = client.get(
        "/v1/disputes/44444444-4444-4444-4444-444444444444",
        headers={"Authorization": "Bearer sk_test"},
    )
    assert resp.status_code == 404
