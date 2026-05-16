"""End-to-end demo flow integration test (Beat 1).

Mocks ALL agent_engine handles so the test runs without GCP credentials,
then drives the full Beat 1 path through the FastAPI gateway:

  1. POST /demo/v1/agent  with onboarding text          -> onboarding
  2. POST /demo/v1/agent  with a draft + region/floor   -> listing_intake
  3. GET  /v1/listings/{id}                             -> joined recommendation

This is the contract CI must protect: the gateway plumbing reaches each
specialist via the Concierge, the static UI's shim doesn't need an API
key, and the listing-fetch route returns the audit fields verbatim.
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


DEMO_PARTNER_ID = "sk_demo_surplus_2026"


@pytest.fixture(autouse=True)
def _seed_resources(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    get_settings.cache_clear()
    monkeypatch.setenv("CONCIERGE_AGENT_RESOURCE",
                       "projects/1/locations/us-central1/reasoningEngines/concierge")
    monkeypatch.setenv("PRICING_AGENT_RESOURCE",
                       "projects/1/locations/us-central1/reasoningEngines/pricing")
    monkeypatch.setenv("ONBOARDING_AGENT_RESOURCE",
                       "projects/1/locations/us-central1/reasoningEngines/onboarding")
    monkeypatch.setenv("LISTING_INTAKE_AGENT_RESOURCE",
                       "projects/1/locations/us-central1/reasoningEngines/intake")
    monkeypatch.setenv("DISPUTE_TRIAGE_AGENT_RESOURCE",
                       "projects/1/locations/us-central1/reasoningEngines/dispute")
    a2a._handle_cache.clear()
    yield
    a2a._handle_cache.clear()
    get_settings.cache_clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    # /demo/v1/* is unauthenticated by design; /v1/* needs a resolved partner.
    app.dependency_overrides[require_partner] = lambda: PartnerContext(
        api_key="sk_test", partner_id=DEMO_PARTNER_ID, context={},
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_beat_1_full_path(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    """Onboard -> list -> fetch the priced listing."""
    new_listing_id = "22222222-2222-2222-2222-222222222222"

    # Concierge handle returns different `specialist_called` payloads per turn.
    # We dispatch on a simple keyword in the merchant message.
    async def fake_call_concierge(**kwargs: Any) -> dict[str, Any]:
        message = kwargs["user_message"].lower()
        if "deli" in message or "tampa" in message:
            return {
                "narration": "Got it — I've set up your profile for Tampa, FL.",
                "specialist_called": "onboarding",
                "specialist_payload": {"merchant_id":
                                       "00000000-0000-0000-0000-000000000001"},
                "event_count": 3,
            }
        if "sandwich" in message or "publish" in message:
            return {
                "narration": "Saved 10 sandwiches at $7.25. "
                             "Expiry pressure dominant at 0.30.",
                "specialist_called": "listing_intake",
                "specialist_payload": {
                    "listing_id": new_listing_id,
                    "recommended_price": 7.25,
                },
                "event_count": 5,
            }
        return {"narration": "I can help you onboard, list, price, or "
                             "resolve a dispute.",
                "specialist_called": None, "specialist_payload": {},
                "event_count": 1}

    monkeypatch.setattr("service.routes_demo.a2a.call_concierge",
                        fake_call_concierge)

    # Turn 1: onboarding.
    resp = client.post("/demo/v1/agent",
                       json={"message": "I'm Tampa Bagel Co, deli in FL."})
    assert resp.status_code == 200
    body1 = resp.json()
    assert body1["specialist_called"] == "onboarding"

    # Turn 2: listing intake.
    resp = client.post("/demo/v1/agent",
                       json={"message": "Save 10 turkey sandwiches, "
                                        "expire 4h, $12 each."})
    assert resp.status_code == 200
    body2 = resp.json()
    assert body2["specialist_called"] == "listing_intake"
    assert body2["specialist_payload"]["listing_id"] == new_listing_id

    # Turn 3: fetch the priced listing through the authenticated REST surface.
    async def fake_fetch_one(_sql: str, *_args: Any) -> dict[str, Any]:
        return {
            "listing_id": new_listing_id,
            "title": "Day-old turkey sandwiches",
            "category": "prepared_meal",
            "units": 10,
            "retail_value": 12.0,
            "hours_until_expiry": 4.0,
            "status": "draft",
            "recommended_price": 7.25,
            "recommended_discount_pct": 0.40,
            "applied_pressures": {"base": 0.10, "expiry": 0.30,
                                  "clamped_to_floor": False},
            "formula_version": "v1",
            "coefficients_version": 7,
        }

    monkeypatch.setattr("service.routes_rest.fetch_one", fake_fetch_one)
    monkeypatch.setattr("service.routes_rest.init_pool", AsyncMock())

    resp = client.get(f"/v1/listings/{new_listing_id}",
                      headers={"Authorization": "Bearer sk_test"})
    assert resp.status_code == 200
    body3 = resp.json()
    assert body3["recommended_price"] == 7.25
    assert body3["applied_pressures"]["expiry"] == 0.30
    assert body3["applied_pressures"]["clamped_to_floor"] is False
    assert body3["formula_version"] == "v1"


def test_demo_agent_out_of_scope_redirect(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    """Concierge's out-of-scope path: no specialist called, narration is a redirect."""
    async def fake_call(**_: Any) -> dict[str, Any]:
        return {
            "narration": "I can help you onboard, list, price, or resolve a dispute.",
            "specialist_called": None,
            "specialist_payload": {},
            "event_count": 1,
        }

    monkeypatch.setattr("service.routes_demo.a2a.call_concierge", fake_call)

    resp = client.post("/demo/v1/agent", json={"message": "tell me a joke"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["specialist_called"] is None
    assert "onboard" in body["narration"].lower()
