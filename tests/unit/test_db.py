"""Unit tests for shared.db pool wiring.

Focus: JSON(B) read-path codec. asyncpg returns JSONB/JSON columns as raw
`str` on read unless a type codec is registered on the connection. Without it,
every read path (`fetch_recommendation_log`, GET /v1/disputes, GET /v1/listings)
surfaces `applied_pressures` / `pressure_diff` / `pricing_input` as JSON strings
instead of dicts — silently breaking the audit round-trip (guardrail #2). Writes
already cast via `$N::jsonb`; this is the read-side counterpart.
"""

from __future__ import annotations

import json
from typing import Any

from shared import db
from shared.config import get_settings


class _FakeConn:
    """Records set_type_codec calls the way asyncpg.Connection would receive them."""

    def __init__(self) -> None:
        self.codecs: dict[str, dict[str, Any]] = {}

    async def set_type_codec(
        self,
        typename: str,
        *,
        encoder: Any,
        decoder: Any,
        schema: str = "public",
        format: str = "text",  # noqa: A002 — matches asyncpg signature
    ) -> None:
        self.codecs[typename] = {
            "encoder": encoder,
            "decoder": decoder,
            "schema": schema,
            "format": format,
        }


class _FakePool:
    async def close(self) -> None:  # pragma: no cover - safety only
        pass


async def test_init_connection_registers_jsonb_and_json_codecs() -> None:
    conn = _FakeConn()

    await db._init_connection(conn)  # type: ignore[arg-type]

    # Both JSON types must be covered (recommendation_log/disputes use jsonb).
    assert "jsonb" in conn.codecs
    assert "json" in conn.codecs

    jsonb = conn.codecs["jsonb"]
    # Decoder turns a JSON string into a Python object (the read-path fix).
    assert jsonb["decoder"]('{"expiry": 0.3, "clamped_to_floor": false}') == {
        "expiry": 0.3,
        "clamped_to_floor": False,
    }
    # Encoder turns a Python object into a JSON string (round-trip on write).
    assert json.loads(jsonb["encoder"]({"a": 1})) == {"a": 1}
    # Pre-serialised writers (json.dumps / model_dump_json at the call site)
    # must pass through untouched — double-encoding stores a JSON *string*
    # scalar instead of an object, breaking the audit round-trip.
    assert jsonb["encoder"]('{"a": 1}') == '{"a": 1}'
    assert json.loads(jsonb["encoder"]('{"a": 1}')) == {"a": 1}
    # jsonb/json are catalog types.
    assert jsonb["schema"] == "pg_catalog"


async def test_init_pool_wires_json_codec_init(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_create_pool(*args: Any, **kwargs: Any) -> _FakePool:
        captured.update(kwargs)
        return _FakePool()

    monkeypatch.setattr(db.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    get_settings.cache_clear()
    db._pool = None
    try:
        await db.init_pool()
        assert captured.get("init") is db._init_connection
    finally:
        db._pool = None
        get_settings.cache_clear()


async def test_init_pool_from_dsn_wires_json_codec_init(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    async def fake_create_pool(*args: Any, **kwargs: Any) -> _FakePool:
        captured.update(kwargs)
        return _FakePool()

    monkeypatch.setattr(db.asyncpg, "create_pool", fake_create_pool)
    db._pool = None
    try:
        await db.init_pool_from_dsn("postgresql://localhost/test")
        assert captured.get("init") is db._init_connection
    finally:
        db._pool = None
