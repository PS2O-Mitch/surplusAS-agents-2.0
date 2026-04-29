"""Async Postgres pool via the Cloud SQL Python Connector.

Single process-wide pool. The connector handles IAM auth and Cloud SQL Auth
Proxy duties without a sidecar process — this matches the pattern used in
`SurplusAS-API-2.0/shared/db.py` and `surplusAS-pricing-intel/db/`.

In tests the pool is initialised against a local Postgres via
`init_pool_from_dsn(dsn)` instead of the connector.
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
from google.cloud.sql.connector import Connector, IPTypes

from shared.config import get_settings

_pool: asyncpg.Pool | None = None
_connector: Connector | None = None
_lock = asyncio.Lock()


async def _create_connection() -> asyncpg.Connection:
    """asyncpg connection factory backed by the Cloud SQL connector."""
    global _connector
    if _connector is None:
        _connector = Connector()

    settings = get_settings()
    return await _connector.connect_async(
        settings.cloud_sql_instance,
        "asyncpg",
        user=settings.db_user,
        password=settings.db_password,
        db=settings.db_name,
        ip_type=IPTypes.PUBLIC,
    )


async def init_pool() -> asyncpg.Pool:
    """Initialise the process-wide pool (Cloud SQL Connector path)."""
    global _pool
    async with _lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(
                connect=_create_connection,
                min_size=1,
                max_size=10,
            )
    assert _pool is not None
    return _pool


async def init_pool_from_dsn(dsn: str) -> asyncpg.Pool:
    """Test/local override: pool against a plain DSN, bypassing the connector."""
    global _pool
    async with _lock:
        if _pool is None:
            _pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=4)
    assert _pool is not None
    return _pool


async def close_pool() -> None:
    """Tear down the pool. Idempotent; safe to call from FastAPI shutdown."""
    global _pool, _connector
    if _pool is not None:
        await _pool.close()
        _pool = None
    if _connector is not None:
        await _connector.close_async()
        _connector = None


def _require_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised; call init_pool() at startup.")
    return _pool


async def fetch_one(query: str, *args: Any) -> dict[str, Any] | None:
    pool = _require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, *args)
        return dict(row) if row else None


async def fetch_all(query: str, *args: Any) -> list[dict[str, Any]]:
    pool = _require_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
        return [dict(r) for r in rows]


async def execute(query: str, *args: Any) -> str:
    pool = _require_pool()
    async with pool.acquire() as conn:
        result: str = await conn.execute(query, *args)
        return result
