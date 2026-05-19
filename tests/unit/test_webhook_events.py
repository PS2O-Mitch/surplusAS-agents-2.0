"""Unit tests for shared.webhook_events.emit_event orchestrator."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    import pytest

from shared.webhook_events import emit_event


async def test_emit_event_writes_delivery_row_and_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shared import webhook_events as we

    monkeypatch.setattr(
        we, "list_active_subscriptions_for_event",
        AsyncMock(return_value=[
            {"subscription_id": "44444444-4444-4444-4444-444444444444",
             "url": "https://a.com",
             "events": ["price.updated"]},
        ]),
    )

    delivered_calls: list[dict[str, Any]] = []

    async def fake_deliver(*, url, payload, signing_key, timeout_s=10.0):
        delivered_calls.append({"url": url, "payload": payload,
                                "signing_key": signing_key})
        return {"status_code": 200}

    monkeypatch.setattr(we, "deliver", fake_deliver)
    monkeypatch.setattr(we, "fetch_one",
                         AsyncMock(return_value={
                             "delivery_id":
                                 "11111111-1111-1111-1111-111111111111"}))
    monkeypatch.setattr(we, "execute", AsyncMock(return_value="UPDATE 1"))
    monkeypatch.setattr(we, "init_pool", AsyncMock())

    out = await emit_event(
        event_type="price.updated",
        partner_id="sk_demo",
        payload={"listing_id": "L-1", "old_price": 7.25, "new_price": 6.50},
    )
    assert out["status"] == "ok"
    assert out["delivery_ids"] == ["11111111-1111-1111-1111-111111111111"]
    assert len(delivered_calls) == 1
    assert delivered_calls[0]["url"] == "https://a.com"
    p = delivered_calls[0]["payload"]
    assert p["event_type"] == "price.updated"
    assert p["partner_id"] == "sk_demo"
    assert p["payload"]["listing_id"] == "L-1"
    assert "event_id" in p and "occurred_at" in p


async def test_emit_event_no_subscriptions_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shared import webhook_events as we
    monkeypatch.setattr(
        we, "list_active_subscriptions_for_event",
        AsyncMock(return_value=[]),
    )
    # If deliver were called we'd notice via this AsyncMock's call_count > 0.
    deliver_mock = AsyncMock()
    monkeypatch.setattr(we, "deliver", deliver_mock)

    out = await emit_event(
        event_type="price.updated", partner_id="sk_demo",
        payload={"x": 1},
    )
    assert out["status"] == "ok"
    assert out["delivery_ids"] == []
    assert deliver_mock.call_count == 0


async def test_emit_event_accepts_non_string_subscription_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: live Cloud SQL returns subscription_id as asyncpg's UUID
    type, not str. Phase 6 e2e surfaced that emit_event was calling
    UUID(sub["subscription_id"]) which raises AttributeError on non-str
    UUID-like values. The str() cast on shared/webhook_events.py keeps this
    code path honest. Stdlib uuid.UUID has the same shape (no .replace),
    so it doubles as a stand-in for asyncpg.pgproto.UUID here.
    """
    import uuid

    from shared import webhook_events as we

    monkeypatch.setattr(
        we, "list_active_subscriptions_for_event",
        AsyncMock(return_value=[
            {"subscription_id":
                 uuid.UUID("44444444-4444-4444-4444-444444444444"),
             "url": "https://a.com", "events": ["price.updated"]},
        ]),
    )
    monkeypatch.setattr(we, "deliver",
                         AsyncMock(return_value={"status_code": 200}))
    monkeypatch.setattr(we, "fetch_one",
                         AsyncMock(return_value={
                             "delivery_id":
                                 "11111111-1111-1111-1111-111111111111"}))
    monkeypatch.setattr(we, "execute", AsyncMock(return_value="UPDATE 1"))
    monkeypatch.setattr(we, "init_pool", AsyncMock())

    out = await emit_event(
        event_type="price.updated", partner_id="sk_demo",
        payload={"x": 1},
    )
    assert out["status"] == "ok"
    assert out["delivery_ids"] == ["11111111-1111-1111-1111-111111111111"]


async def test_emit_event_records_failure_status_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When delivery fails (non-2xx), the delivery row gets last_status_code
    + last_error and delivered_at stays NULL — but emit_event still returns
    ok with the delivery_id for audit."""
    from shared import webhook_events as we

    monkeypatch.setattr(
        we, "list_active_subscriptions_for_event",
        AsyncMock(return_value=[
            {"subscription_id": "44444444-4444-4444-4444-444444444444",
             "url": "https://a.com", "events": ["price.updated"]},
        ]),
    )

    monkeypatch.setattr(
        we, "deliver",
        AsyncMock(return_value={"status_code": 502, "error": "bad gateway"}),
    )

    captured_updates: list[tuple[str, tuple[Any, ...]]] = []

    async def fake_execute(sql: str, *args: Any) -> str:
        captured_updates.append((sql, args))
        return "UPDATE 1"

    monkeypatch.setattr(we, "fetch_one",
                         AsyncMock(return_value={
                             "delivery_id":
                                 "11111111-1111-1111-1111-111111111111"}))
    monkeypatch.setattr(we, "execute", fake_execute)
    monkeypatch.setattr(we, "init_pool", AsyncMock())

    out = await emit_event(
        event_type="price.updated", partner_id="sk_demo",
        payload={"x": 1},
    )
    assert out["status"] == "ok"
    assert out["delivery_ids"] == ["11111111-1111-1111-1111-111111111111"]
    # The failure-update SQL must NOT set delivered_at
    sql, args = captured_updates[0]
    assert "last_status_code" in sql
    assert "last_error" in sql
    assert "delivered_at" not in sql.replace("RETURNING", "")  # not in UPDATE SET clause
    assert 502 in args
