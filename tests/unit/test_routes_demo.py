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


# --- /demo/v1/listings/generate + /publish (intake-direct + DB enrichment) ---

def _persist_ok(listing_id: str = "L-1") -> dict[str, Any]:
    return {"name": "persist_listing", "args": {},
            "response": {"status": "ok", "listing_id": listing_id,
                         "recommendation_id": "R-1", "listing_status": "draft"}}


_ENRICHED_ROW = {
    "listing_id": "L-1", "title": "Croissants", "description": "Fresh",
    "category": "bakery", "units": 20, "retail_value": 3.50,
    "hours_until_expiry": 2.0, "status": "draft",
    "recommendation_id": "R-1", "recommended_price": 1.23,
    "recommended_discount_pct": 0.65, "anchor_p50": 3.00,
    "applied_pressures": {"expiry_pressure": 0.30}, "formula_version": "v1.2",
}

_FULL_LISTING_BODY = {
    "title": "Croissants", "description": "Fresh", "category": "bakery",
    "units": 20, "retail_value": 3.5, "hours_until_expiry": 2.0,
}


def _wire_intake(monkeypatch: pytest.MonkeyPatch,
                 tool_calls: list[dict[str, Any]],
                 row: dict[str, Any] | None) -> dict[str, Any]:
    """Patch aggregate + DB for the intake-direct routes; returns capture dict."""
    captured: dict[str, Any] = {"db_args": None, "aggregate_calls": 0}

    async def fake_aggregate(peer: str, user_message: str,
                             partner_id: str) -> dict[str, Any]:
        captured["aggregate_calls"] += 1
        captured["peer"] = peer
        captured["user_message"] = user_message
        captured["partner_id"] = partner_id
        return {"narration": "done", "tool_calls": tool_calls, "event_count": 5}

    async def fake_init_pool() -> None:
        return None

    async def fake_fetch_one(_sql: str, *args: Any) -> dict[str, Any] | None:
        captured["db_args"] = args
        return row

    monkeypatch.setattr("service.routes_demo.a2a.aggregate_peer_stream",
                        fake_aggregate)
    monkeypatch.setattr("service.routes_demo.init_pool", fake_init_pool)
    monkeypatch.setattr("service.routes_demo.fetch_one", fake_fetch_one)
    return captured


def test_demo_generate_requires_note(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    captured = _wire_intake(monkeypatch, [], None)
    for body in ({}, {"note": "   "}):
        resp = client.post("/demo/v1/listings/generate", json=body)
        assert resp.status_code == 200
        assert resp.json() == {"error": "note is required"}
    assert captured["aggregate_calls"] == 0


def test_demo_generate_routes_to_intake_with_context(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    captured = _wire_intake(monkeypatch, [_persist_ok()], _ENRICHED_ROW)

    async def fake_merchant_id() -> str | None:
        return "m-demo"

    monkeypatch.setattr("service.routes_demo._demo_merchant_id", fake_merchant_id)

    resp = client.post("/demo/v1/listings/generate",
                       json={"note": "20 croissants left"})
    assert resp.status_code == 200
    assert captured["peer"] == "listing_intake"
    assert captured["partner_id"] == "demo_001"
    assert captured["user_message"] == (
        "[partner_id=demo_001, merchant_id=m-demo] 20 croissants left"
    )


def test_demo_generate_clarification_when_no_persist(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    tool_calls = [
        {"name": "parse_draft", "args": {}, "response": {"status": "ok"}},
        {"name": "validate_listing", "args": {},
         "response": {"status": "validation_error",
                      "errors": [{"field": "retail_value", "error": "missing"}]}},
    ]
    captured = _wire_intake(monkeypatch, tool_calls, _ENRICHED_ROW)

    resp = client.post("/demo/v1/listings/generate", json={"note": "some pastries"})
    body = resp.json()
    assert body == {"status": "clarification", "narration": "done"}
    assert captured["db_args"] is None  # no read-back without a persisted row


def test_demo_generate_enriches_persisted_listing(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    captured = _wire_intake(monkeypatch, [_persist_ok()], _ENRICHED_ROW)

    resp = client.post("/demo/v1/listings/generate", json={"note": "20 croissants"})
    body = resp.json()
    assert body["status"] == "ok"
    assert body["narration"] == "done"
    assert body["listing"]["listing_id"] == "L-1"
    assert body["listing"]["status"] == "draft"
    assert body["pricing"]["recommended_price"] == 1.23
    assert body["pricing"]["applied_pressures"] == {"expiry_pressure": 0.30}
    assert body["pricing"]["formula_version"] == "v1.2"
    assert captured["db_args"] == ("L-1", "demo_001")


def test_demo_generate_uses_last_successful_persist(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    tool_calls = [
        {"name": "persist_listing", "args": {},
         "response": {"status": "validation_error", "error": "bad uuid"}},
        _persist_ok("L-2"),
    ]
    captured = _wire_intake(monkeypatch, tool_calls,
                            {**_ENRICHED_ROW, "listing_id": "L-2"})

    resp = client.post("/demo/v1/listings/generate", json={"note": "20 croissants"})
    assert resp.json()["listing"]["listing_id"] == "L-2"
    assert captured["db_args"] == ("L-2", "demo_001")


def test_demo_generate_error_when_readback_fails(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    _wire_intake(monkeypatch, [_persist_ok()], None)

    resp = client.post("/demo/v1/listings/generate", json={"note": "20 croissants"})
    body = resp.json()
    assert body["status"] == "error"
    assert body["listing_id"] == "L-1"
    assert "read back" in body["error"]


def test_demo_publish_requires_listing_fields(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    captured = _wire_intake(monkeypatch, [], None)

    resp = client.post("/demo/v1/listings/publish", json={})
    assert resp.json() == {"error": "listing object is required"}

    resp = client.post("/demo/v1/listings/publish",
                       json={"listing": {"title": "x"}})
    err = resp.json()["error"]
    for field in ("category", "units", "retail_value", "hours_until_expiry"):
        assert field in err
    assert captured["aggregate_calls"] == 0


def test_demo_publish_composes_publish_message(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    captured = _wire_intake(
        monkeypatch, [_persist_ok()],
        {**_ENRICHED_ROW, "status": "published"},
    )

    resp = client.post("/demo/v1/listings/publish",
                       json={"listing": _FULL_LISTING_BODY})
    body = resp.json()
    msg = captured["user_message"]
    assert msg.startswith("[partner_id=demo_001]")
    assert "status='published'" in msg
    assert "title: Croissants" in msg
    assert "retail_value: 3.5" in msg
    assert body["status"] == "ok"
    assert body["listing"]["status"] == "published"


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
