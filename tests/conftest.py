"""Shared pytest fixtures.

Process-wide fixtures live here so they fire across both `tests/unit/`
and `tests/integration/`: the A2A runner-cache reset (a cached `Runner`
from one test would leak into the next) and the demo rate-limiter reset
(every TestClient request shares one fake IP, so leftover windows would
429 later tests).
"""

from __future__ import annotations

import os

import pytest

from service import routes_demo
from shared import a2a

# The demo shim is opt-in (settings.demo_mode, off in prod); the demo-route
# and e2e suites exercise it. Set before any test module builds the app.
os.environ.setdefault("DEMO_MODE", "true")


@pytest.fixture(autouse=True)
def _clear_a2a_runner_cache() -> None:
    """Empty `_runner_cache` before and after every test.

    Cheap, defensive, and saves every test that monkeypatches
    `_get_runner` / `_build_runner` from having to remember to clear the
    cache itself.
    """
    a2a._runner_cache.clear()
    yield
    a2a._runner_cache.clear()


@pytest.fixture(autouse=True)
def _clear_demo_rate_limiter() -> None:
    """Empty the demo rate-limit windows before and after every test."""
    routes_demo._ip_hits.clear()
    routes_demo._global_hits.clear()
    yield
    routes_demo._ip_hits.clear()
    routes_demo._global_hits.clear()
