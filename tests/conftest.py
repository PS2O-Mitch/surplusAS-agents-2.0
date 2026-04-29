"""Shared pytest fixtures.

Most fixtures (asyncpg pool, partner_keys seed, ADK runner) land in Week 2+.
For Week 1 the fixtures here are deliberately minimal so CI can run before
the agent code exists.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
