"""Unit tests for the unauthenticated demo shim routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from service.app import create_app

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _no_demo_merchant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the shim's demo-merchant lookup away from a real database."""
    async def _none() -> str | None:
        return None

    monkeypatch.setattr("service.routes_demo._demo_merchant_id", _none)


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
    assert captured["partner_id"] == "demo_001"
    assert captured["user_message"] == (
        "[partner_id=demo_001] I run a deli"
    )


def test_demo_agent_injects_demo_merchant_id(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    """When the page sends no merchant_id, the shim injects the demo merchant's."""
    captured: dict[str, Any] = {}

    async def fake_call_concierge(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"narration": "ok", "specialist_called": "listing_intake",
                "specialist_payload": {}, "event_count": 1}

    async def fake_merchant_id() -> str | None:
        return "be1ce99c-demo"

    monkeypatch.setattr("service.routes_demo._demo_merchant_id",
                        fake_merchant_id)
    monkeypatch.setattr("service.routes_demo.a2a.call_concierge",
                        fake_call_concierge)

    resp = client.post("/demo/v1/agent", json={"message": "20 croissants left"})
    assert resp.status_code == 200
    assert captured["user_message"] == (
        "[partner_id=demo_001, merchant_id=be1ce99c-demo] 20 croissants left"
    )


def test_demo_agent_accepts_legacy_input_field(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    """The bundled static page sends `{mode, input}` (API-2.0 era), not `{message}`."""
    captured: dict[str, Any] = {}

    async def fake_call_concierge(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"narration": "ok", "specialist_called": "onboarding",
                "specialist_payload": {}, "event_count": 1}

    monkeypatch.setattr("service.routes_demo.a2a.call_concierge",
                        fake_call_concierge)

    resp = client.post(
        "/demo/v1/agent",
        json={"mode": "listing_create", "input": "I run a deli in Tampa, FL"},
    )
    assert resp.status_code == 200
    assert captured["user_message"] == (
        "[partner_id=demo_001] I run a deli in Tampa, FL"
    )


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
        "[partner_id=demo_001, merchant_id=m-1, listing_id=abc-123] "
        "why is the price low?"
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
    assert captured["partner_id"] == "demo_001"


def test_demo_agent_requires_no_auth(client: TestClient,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Same-origin shim is intentionally unauthenticated (DEMO_MODE gates it)."""
    async def fake_call(**_: Any) -> dict[str, Any]:
        return {"narration": "ok", "specialist_called": None,
                "specialist_payload": {}, "event_count": 1}

    monkeypatch.setattr("service.routes_demo.a2a.call_concierge", fake_call)
    resp = client.post("/demo/v1/agent", json={"message": "hi"})
    assert resp.status_code == 200


def test_demo_open_dispute_routes_to_triage(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_aggregate(peer: str, user_message: str,
                               partner_id: str) -> dict[str, Any]:
        captured["peer"] = peer
        captured["user_message"] = user_message
        captured["partner_id"] = partner_id
        return {
            "narration": "Replayed. New price $5.95.",
            "tool_calls": [
                {"name": "fetch_recommendation_log"},
                {"name": "replay_recommendation"},
                {"name": "diff_pressures"},
                {"name": "persist_dispute"},
            ],
            "event_count": 8,
        }

    monkeypatch.setattr("service.routes_demo.a2a.aggregate_peer_stream",
                        fake_aggregate)

    resp = client.post(
        "/demo/v1/listings/L-abc/dispute",
        json={"reason": "Customer thinks price is too high"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["listing_id"] == "L-abc"
    assert "Replayed" in body["narration"]
    assert {tc["name"] for tc in body["tool_calls"]} == {
        "fetch_recommendation_log", "replay_recommendation",
        "diff_pressures", "persist_dispute",
    }
    assert captured["peer"] == "dispute_triage"
    assert captured["partner_id"] == "demo_001"
    assert "L-abc" in captured["user_message"]
    assert "too high" in captured["user_message"]


def test_demo_surface_allows_file_origin(client: TestClient) -> None:
    """file:// opens send Origin: null; demo CORS must let the page through."""
    resp = client.options(
        "/demo/v1/agent",
        headers={"Origin": "null",
                 "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "content-type"},
    )
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "*"


def test_demo_open_dispute_requires_reason(client: TestClient) -> None:
    resp = client.post(
        "/demo/v1/listings/L-abc/dispute",
        json={"reason": "   "},
    )
    assert resp.status_code == 200
    assert resp.json() == {"error": "reason is required"}
