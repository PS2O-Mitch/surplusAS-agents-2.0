"""FastAPI gateway factory.

Mounts the public REST surface, the demo compatibility shim, the inbound A2A
dispatcher, and the same-origin static UI. Tracing + logging are initialised
on app startup.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from shared.config import get_settings
from shared.logging import init_logging
from shared.tracing import init_tracing
from shared.webhook_retry import retry_failed_deliveries

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

STATIC_DIR = Path(__file__).parent / "static"

_log = logging.getLogger("surplusas.gateway.webhook_retry")


async def _webhook_retry_loop() -> None:
    """Periodically sweep failed webhook deliveries.

    Phase 6 ships this as an in-process background task on the gateway.
    Phase 7+ can extract to a dedicated Cloud Run job if redelivery SLOs
    demand independent scaling. A failed sweep is logged-and-continued —
    a transient DB or HTTP failure must not kill the loop, otherwise we
    silently lose retry coverage until the gateway restarts.
    """
    settings = get_settings()
    interval = settings.webhook_retry_interval_s
    limit = settings.webhook_retry_batch_limit

    while True:
        try:
            await retry_failed_deliveries(limit=limit)
        except Exception as exc:  # noqa: BLE001 — log but never kill the loop
            _log.warning("webhook retry sweep failed: %s", exc, exc_info=True)
        await asyncio.sleep(interval)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    init_logging(settings.log_level)
    init_tracing("surplusas-agents-gateway")
    # DB pool initialised lazily on first use; tests override this entirely.
    retry_task = asyncio.create_task(
        _webhook_retry_loop(), name="webhook-retry-loop",
    )
    try:
        yield
    finally:
        retry_task.cancel()
        with suppress(asyncio.CancelledError):
            await retry_task


def create_app() -> FastAPI:
    app = FastAPI(
        title="surplusAS-agents-2.0",
        version="0.1.0",
        description="Multi-agent service for SurplusAS on Vertex AI Agent Engine.",
        lifespan=_lifespan,
    )

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    from .routes_demo import router as demo_router
    from .routes_disputes import router as disputes_router
    from .routes_rest import router as rest_router
    from .routes_webhooks import router as webhooks_router
    app.include_router(rest_router)
    app.include_router(disputes_router)
    app.include_router(webhooks_router)
    app.include_router(demo_router)

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


app = create_app()
