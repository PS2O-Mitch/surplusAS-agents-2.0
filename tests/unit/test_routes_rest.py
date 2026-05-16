"""Unit tests for `service.routes_rest`.

The public REST surface is gated by `shared.auth.require_partner`. Tests
override that dependency directly via `app.dependency_overrides` so they
don't need a real DB or token. All A2A calls and DB lookups are mocked.
"""

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


def test_post_concierge_invokes_concierge_handle(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_call_peer_agent(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {
            "narration": "Routed to Listing Intake; saved 10 sandwiches at $7.25.",
            "specialist_called": "listing_intake",
            "specialist_payload": {"listing_id": "abc"},
        }

    monkeypatch.setattr("service.routes_rest.a2a.call_peer_agent",
                        fake_call_peer_agent)

    resp = client.post(
        "/v1/concierge",
        json={"partner_id": "sk_demo",
              "message": "10 sandwiches expire in 4h"},
        headers={"Authorization": "Bearer sk_test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["narration"].startswith("Routed to Listing Intake")
    assert body["specialist_called"] == "listing_intake"
    assert captured["peer"] == "concierge"
    assert captured["partner_id"] == "sk_demo"


def test_post_concierge_rejects_partner_id_mismatch(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    async def fake_call_peer_agent(**_: Any) -> dict[str, Any]:
        # Should never reach here — body partner_id != authenticated partner_id.
        return {"narration": "should not be called"}

    monkeypatch.setattr("service.routes_rest.a2a.call_peer_agent",
                        fake_call_peer_agent)

    resp = client.post(
        "/v1/concierge",
        json={"partner_id": "sk_other", "message": "x"},
        headers={"Authorization": "Bearer sk_test"},
    )
    assert resp.status_code == 403


def test_get_listing_returns_joined_recommendation(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    async def fake_fetch_one(_sql: str, *_args: Any) -> dict[str, Any]:
        return {
            "listing_id": "abc",
            "title": "Day-old sandwiches",
            "category": "prepared_meal",
            "units": 10,
            "retail_value": 12.0,
            "status": "draft",
            "recommended_price": 7.25,
            "applied_pressures": {"base": 0.10, "expiry": 0.30,
                                  "clamped_to_floor": False},
            "formula_version": "v1",
        }

    monkeypatch.setattr("service.routes_rest.fetch_one", fake_fetch_one)
    monkeypatch.setattr("service.routes_rest.init_pool", AsyncMock())

    resp = client.get(
        "/v1/listings/22222222-2222-2222-2222-222222222222",
        headers={"Authorization": "Bearer sk_test"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["recommended_price"] == 7.25
    assert body["applied_pressures"]["clamped_to_floor"] is False


def test_get_listing_returns_404_when_not_found(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    monkeypatch.setattr("service.routes_rest.fetch_one",
                        AsyncMock(return_value=None))
    monkeypatch.setattr("service.routes_rest.init_pool", AsyncMock())

    resp = client.get(
        "/v1/listings/22222222-2222-2222-2222-222222222222",
        headers={"Authorization": "Bearer sk_test"},
    )
    assert resp.status_code == 404
