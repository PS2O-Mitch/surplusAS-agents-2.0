# tests/unit/test_webhook_retry.py
"""Unit tests for shared.webhook_retry.retry_failed_deliveries."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    import pytest

from shared.webhook_retry import retry_failed_deliveries


async def test_retry_skips_when_no_failed_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty candidate set returns the zero-summary."""
    from shared import webhook_retry as wr
    monkeypatch.setattr(wr, "fetch_all", AsyncMock(return_value=[]))
    monkeypatch.setattr(wr, "init_pool", AsyncMock())
    out = await retry_failed_deliveries(limit=100)
    assert out == {"scanned": 0, "retried": 0, "succeeded": 0,
                   "failed": 0, "dead_lettered": 0}


async def test_retry_redelivers_eligible_row_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row whose backoff window has passed gets redelivered; 2xx
    UPDATEs attempt += 1, last_attempt_at = NOW(), delivered_at = NOW()."""
    from shared import webhook_retry as wr

    candidate = {
        "delivery_id": "11111111-1111-1111-1111-111111111111",
        "subscription_id": "44444444-4444-4444-4444-444444444444",
        "url": "https://example.com/hook",
        "event_type": "price.updated",
        "payload": {"event_id": "e-1", "event_type": "price.updated",
                    "partner_id": "sk_demo", "occurred_at": "2026-05-17T00:00:00Z",
                    "payload": {"x": 1}},
        "attempt": 1,
    }
    monkeypatch.setattr(wr, "fetch_all", AsyncMock(return_value=[candidate]))
    monkeypatch.setattr(wr, "init_pool", AsyncMock())

    captured_updates: list[tuple[str, tuple[Any, ...]]] = []

    async def fake_execute(sql: str, *args: Any) -> str:
        captured_updates.append((sql, args))
        return "UPDATE 1"

    monkeypatch.setattr(wr, "execute", fake_execute)
    monkeypatch.setattr(wr, "deliver",
                        AsyncMock(return_value={"status_code": 200}))

    out = await retry_failed_deliveries(limit=100)
    assert out["scanned"] == 1
    assert out["retried"] == 1
    assert out["succeeded"] == 1
    assert out["failed"] == 0
    assert out["dead_lettered"] == 0

    sql, _args = captured_updates[0]
    assert "UPDATE agents.webhook_deliveries" in sql
    assert "attempt = attempt + 1" in sql
    assert "delivered_at = NOW()" in sql
    assert "last_attempt_at = NOW()" in sql


async def test_retry_marks_failed_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-2xx UPDATEs attempt + last_attempt_at + last_status_code + last_error
    but does NOT set delivered_at."""
    from shared import webhook_retry as wr

    monkeypatch.setattr(wr, "fetch_all", AsyncMock(return_value=[{
        "delivery_id": "11111111-1111-1111-1111-111111111111",
        "subscription_id": "44444444-4444-4444-4444-444444444444",
        "url": "https://example.com/hook",
        "event_type": "price.updated",
        "payload": {"event_id": "e-1"},
        "attempt": 2,
    }]))
    monkeypatch.setattr(wr, "init_pool", AsyncMock())

    captured: list[tuple[str, tuple[Any, ...]]] = []

    async def fake_execute(sql: str, *args: Any) -> str:
        captured.append((sql, args))
        return "UPDATE 1"

    monkeypatch.setattr(wr, "execute", fake_execute)
    monkeypatch.setattr(wr, "deliver",
                        AsyncMock(return_value={"status_code": 503,
                                                 "error": "service unavailable"}))

    out = await retry_failed_deliveries(limit=100)
    assert out["succeeded"] == 0
    assert out["failed"] == 1
    assert out["retried"] == 1

    sql, _args = captured[0]
    assert "last_status_code" in sql
    assert "last_error" in sql
    # Failure path must NOT set delivered_at
    set_clause = sql.split("WHERE")[0]
    assert "delivered_at" not in set_clause


async def test_retry_dead_letters_at_max_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row that fails AGAIN when attempt is already 4 (becoming 5) gets
    marked as dead-lettered in the return summary."""
    from shared import webhook_retry as wr

    monkeypatch.setattr(wr, "fetch_all", AsyncMock(return_value=[{
        "delivery_id": "11111111-1111-1111-1111-111111111111",
        "subscription_id": "44444444-4444-4444-4444-444444444444",
        "url": "https://example.com/hook",
        "event_type": "price.updated",
        "payload": {"event_id": "e-1"},
        "attempt": 4,  # this attempt will be the 5th
    }]))
    monkeypatch.setattr(wr, "init_pool", AsyncMock())
    monkeypatch.setattr(wr, "execute", AsyncMock(return_value="UPDATE 1"))
    monkeypatch.setattr(wr, "deliver",
                        AsyncMock(return_value={"status_code": 500,
                                                 "error": "x"}))

    out = await retry_failed_deliveries(limit=100)
    assert out["failed"] == 1
    assert out["dead_lettered"] == 1


async def test_retry_respects_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The limit parameter reaches the SELECT (either as a $N placeholder
    arg or as a literal LIMIT in the SQL)."""
    from shared import webhook_retry as wr

    captured_args: dict[str, Any] = {}

    async def fake_fetch_all(sql: str, *args: Any) -> list[dict[str, Any]]:
        captured_args["sql"] = sql
        captured_args["args"] = args
        return []

    monkeypatch.setattr(wr, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(wr, "init_pool", AsyncMock())
    monkeypatch.setattr(wr, "execute", AsyncMock())
    monkeypatch.setattr(wr, "deliver", AsyncMock())

    await retry_failed_deliveries(limit=42)
    # The limit must appear in the SQL or args.
    assert 42 in captured_args["args"] or "LIMIT 42" in captured_args["sql"]


async def test_retry_payload_as_string_is_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSONB columns can be returned as string when asyncpg has no codec
    registered. The worker must json.loads strings before passing to deliver."""
    import json

    from shared import webhook_retry as wr

    full_payload = {"event_id": "e-1", "event_type": "x",
                    "partner_id": "p", "occurred_at": "2026-05-17T00:00:00Z",
                    "payload": {"foo": "bar"}}

    monkeypatch.setattr(wr, "fetch_all", AsyncMock(return_value=[{
        "delivery_id": "11111111-1111-1111-1111-111111111111",
        "subscription_id": "44444444-4444-4444-4444-444444444444",
        "url": "https://example.com/hook",
        "event_type": "x",
        "payload": json.dumps(full_payload),  # stored as string
        "attempt": 1,
    }]))
    monkeypatch.setattr(wr, "init_pool", AsyncMock())
    monkeypatch.setattr(wr, "execute", AsyncMock(return_value="UPDATE 1"))

    captured_payload: dict[str, Any] = {}

    async def fake_deliver(*, url, payload, signing_key, timeout_s=10.0):
        captured_payload.update(payload)
        return {"status_code": 200}

    monkeypatch.setattr(wr, "deliver", fake_deliver)

    out = await retry_failed_deliveries(limit=100)
    assert out["succeeded"] == 1
    # deliver received the parsed dict, not the JSON string.
    assert captured_payload["event_id"] == "e-1"
    assert captured_payload["payload"] == {"foo": "bar"}
