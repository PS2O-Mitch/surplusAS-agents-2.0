"""Hello-world tests that gate Week 1 CI.

These prove that:
1. The Python package layout imports cleanly.
2. Settings can be constructed in a no-secret environment.
3. The FastAPI app boots and serves /healthz.

When real agent code lands, replace this file with per-module unit tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

from service.app import create_app
from shared.config import Settings

if TYPE_CHECKING:
    import pytest


def test_settings_construct_without_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hermetic: another module's get_settings() may have mirrored a dev
    # machine's .env values into os.environ before this test runs.
    monkeypatch.delenv("GOOGLE_GENAI_USE_VERTEXAI", raising=False)
    s = Settings(_env_file=None)
    assert s.google_genai_use_vertexai is False
    assert s.concierge_model == "gemini-3.1-pro-preview"
    assert s.pricing_model == "gemini-3.5-flash"


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
