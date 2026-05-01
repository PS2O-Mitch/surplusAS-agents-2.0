"""Shared pytest fixtures.

Process-wide fixtures live here so they fire across both `tests/unit/`
and `tests/integration/`. Today the only one is the A2A handle-cache
reset — without it, a cached `AgentEngine` from one test leaks into the
next, breaking isolation when both `test_a2a.py` and the integration
test hit `shared.a2a` in the same session.
"""

from __future__ import annotations

import pytest

from shared import a2a


@pytest.fixture(autouse=True)
def _clear_a2a_handle_cache() -> None:
    """Empty `_handle_cache` before and after every test.

    Cheap, defensive, and saves every test that monkeypatches
    `agent_engines.get` from having to remember to clear the cache itself.
    """
    a2a._handle_cache.clear()
    yield
    a2a._handle_cache.clear()
