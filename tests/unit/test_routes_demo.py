"""Unit tests for the unauthenticated demo shim routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from service.app import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def test_demo_agent_uses_demo_partner_id(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_call_concierge(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"narration": "demo ok", "specialist_called": "onboarding",
                "specialist_payload": {"merchant_id": "abc"},
                "event_count": 3}

    monkeypatch.setattr("service.routes_demo.a2a.call_concierge",
                        fake_call_concierge)

    resp = client.post("/demo/v1/agent", json={"message": "I run a deli"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["narration"] == "demo ok"
    assert body["specialist_called"] == "onboarding"
    assert body["specialist_payload"] == {"merchant_id": "abc"}
    assert captured["partner_id"] == "sk_demo_surplus_2026"
    assert captured["user_message"] == "I run a deli"


def test_demo_agent_prepends_context_to_message(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_call_concierge(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"narration": "ok", "specialist_called": None,
                "specialist_payload": {}, "event_count": 1}

    monkeypatch.setattr("service.routes_demo.a2a.call_concierge",
                        fake_call_concierge)

    resp = client.post(
        "/demo/v1/agent",
        json={"message": "why is the price low?",
              "listing_id": "abc-123", "merchant_id": "m-1"},
    )
    assert resp.status_code == 200
    assert captured["user_message"] == (
        "[merchant_id=m-1, listing_id=abc-123] why is the price low?"
    )


def test_demo_publish_listing_routes_to_intake(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_call(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"status": "ok", "listing_id": "abc"}

    monkeypatch.setattr("service.routes_demo.a2a.call_peer_agent", fake_call)

    resp = client.post(
        "/demo/v1/listings/publish",
        json={"draft": {"title": "x"}, "recommendation_id": "rec-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["listing_id"] == "abc"
    assert captured["peer"] == "listing_intake"
    assert captured["mode"] == "publish"
    assert captured["partner_id"] == "sk_demo_surplus_2026"


def test_demo_agent_requires_no_auth(client: TestClient,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Same-origin shim is intentionally unauthenticated (Cloud Run IAP gates static)."""
    async def fake_call(**_: Any) -> dict[str, Any]:
        return {"narration": "ok", "specialist_called": None,
                "specialist_payload": {}, "event_count": 1}

    monkeypatch.setattr("service.routes_demo.a2a.call_concierge", fake_call)
    resp = client.post("/demo/v1/agent", json={"message": "hi"})
    assert resp.status_code == 200
