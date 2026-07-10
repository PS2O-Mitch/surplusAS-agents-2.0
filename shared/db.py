"""Async Postgres pool over a plain DSN (`DATABASE_URL`).

Single process-wide pool. Production points at Supabase — use the
session-mode pooler or the direct connection string; transaction-mode
pgBouncer (port 6543) breaks asyncpg prepared statements.

In tests the pool is initialised against a local Postgres via
`init_pool_from_dsn(dsn)` directly.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg

from shared.config import get_settings

_pool: asyncpg.Pool | None = None
_lock = asyncio.Lock()


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Per-connection setup: register JSON(B) codecs so JSONB columns
    round-trip as Python objects.

    Without this, asyncpg hands JSONB/JSON columns back as raw ``str`` on the
    read side, so every read path (`fetch_recommendation_log`, the dispute and
    listing REST GETs) would surface `applied_pressures` / `pressure_diff` /
    `pricing_input` as JSON strings instead of dicts — silently breaking the
    audit round-trip (CLAUDE.md guardrail #2). The write paths already cast via
    ``$N::jsonb`` with ``json.dumps``; this is the read-side counterpart.
    """
    for typename in ("jsonb", "json"):
        await conn.set_type_codec(
            typename,
            encoder=json.dumps,
            decoder=json.loads,
            schema="pg_catalog",
        )


async def init_pool() -> asyncpg.Pool:
    """Initialise the process-wide pool from `DATABASE_URL`."""
    dsn = get_settings().database_url
    if not dsn:
        raise RuntimeError("DATABASE_URL not set; required for the asyncpg pool.")
    return await init_pool_from_dsn(dsn)


async def init_pool_from_dsn(dsn: str) -> asyncpg.Pool:
    """Pool against a plain DSN. `init_pool()` delegates here; tests call it
    directly with a local-Postgres DSN. `max_size=4` respects Supabase's
    session-pooler connection budget on the entry tier."""
    global _pool
    async with _lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(
                dsn=dsn, init=_init_connection, min_size=1, max_size=4,
            )
    assert _pool is not None
    return _pool


async def close_pool() -> None:
    """Tear down the pool. Idempotent; safe to call from FastAPI shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def require_pool() -> asyncpg.Pool:
    """Return the process-wide pool. Raises if `init_pool()` hasn't run yet.

    Public on purpose: every caller that needs a connection (the pricing
    adapter, the gateway routes, ad-hoc scripts) goes through here so the
    "you forgot to initialise the pool" error is uniform across the repo.
    """
    if _pool is None:
        raise RuntimeError("DB pool not initialised; call init_pool() at startup.")
    return _pool


async def fetch_one(query: str, *args: Any) -> dict[str, Any] | None:
    pool = require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, *args)
        return dict(row) if row else None


async def fetch_all(query: str, *args: Any) -> list[dict[str, Any]]:
    pool = require_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
        return [dict(r) for r in rows]


async def execute(query: str, *args: Any) -> str:
    pool = require_pool()
    async with pool.acquire() as conn:
        result: str = await conn.execute(query, *args)
        return result
