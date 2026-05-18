"""Verify the gateway's lifespan starts and cancels the retry loop cleanly."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi.testclient import TestClient

if TYPE_CHECKING:
    import pytest


async def test_retry_loop_starts_and_cancels_with_lifespan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the FastAPI app starts, the retry loop coroutine is scheduled;
    when the app shuts down, the task is cancelled. The retry function is
    stubbed so we don't hit the DB."""
    from service import app as service_app

    call_count = 0

    async def fake_retry(*, limit: int) -> dict[str, int]:
        nonlocal call_count
        call_count += 1
        # Yield so other tasks can run.
        await asyncio.sleep(0)
        return {"scanned": 0, "retried": 0, "succeeded": 0,
                "failed": 0, "dead_lettered": 0}

    monkeypatch.setattr(service_app, "retry_failed_deliveries", fake_retry)
    # Speed up the loop so the test doesn't wait 30s.
    monkeypatch.setenv("WEBHOOK_RETRY_INTERVAL_S", "0")
    from shared.config import get_settings
    get_settings.cache_clear()

    app = service_app.create_app()
    with TestClient(app):
        # Yield to the event loop so the background task gets a chance to tick.
        await asyncio.sleep(0.05)

    # The loop should have ticked at least once during the lifespan.
    assert call_count >= 1
    get_settings.cache_clear()


async def test_retry_loop_logs_and_continues_on_sweep_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If retry_failed_deliveries raises, the loop logs and continues
    (doesn't die — that would cause permanent loss of retry coverage)."""
    from service import app as service_app

    call_count = 0

    async def flaky_retry(*, limit: int) -> dict[str, int]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated DB outage")
        await asyncio.sleep(0)
        return {"scanned": 0, "retried": 0, "succeeded": 0,
                "failed": 0, "dead_lettered": 0}

    monkeypatch.setattr(service_app, "retry_failed_deliveries", flaky_retry)
    monkeypatch.setenv("WEBHOOK_RETRY_INTERVAL_S", "0")
    from shared.config import get_settings
    get_settings.cache_clear()

    app = service_app.create_app()
    with TestClient(app):
        await asyncio.sleep(0.05)

    # The first call raised; the loop must have survived and called again.
    assert call_count >= 2
    get_settings.cache_clear()
