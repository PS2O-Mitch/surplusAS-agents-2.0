"""Hello-world tests that gate Week 1 CI.

These prove that:
1. The Python package layout imports cleanly.
2. Settings can be constructed in a no-secret environment.
3. The FastAPI app boots and serves /healthz.

When real agent code lands, replace this file with per-module unit tests.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from service.app import create_app
from shared.config import Settings


def test_settings_construct_without_secrets() -> None:
    s = Settings(_env_file=None)
    assert s.google_cloud_project == "ps2o-surplusas-api"
    assert s.google_cloud_location == "us-central1"
    assert s.concierge_model == "gemini-2.5-pro"
    assert s.pricing_model == "gemini-2.5-flash"


def test_app_health_check() -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_agent_packages_import() -> None:
    """All five agent subpackages must be importable from a fresh checkout."""
    import agents.concierge.agent as concierge
    import agents.dispute_triage.agent as dispute_triage
    import agents.listing_intake.agent as listing_intake
    import agents.onboarding.agent as onboarding
    import agents.pricing.agent as pricing

    assert concierge.AGENT_NAME == "concierge"
    assert pricing.AGENT_NAME == "pricing"
    assert onboarding.AGENT_NAME == "onboarding"
    assert listing_intake.AGENT_NAME == "listing_intake"
    assert dispute_triage.AGENT_NAME == "dispute_triage"
