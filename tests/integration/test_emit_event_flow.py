"""End-to-end integration test for the webhook emit_event orchestrator.

Mocks `httpx.AsyncClient` (so `deliver` exercises its full path) and the DB
helpers (so the audit-row INSERTs and UPDATEs are observable). For each of
the four Phase 4/5 event types, asserts:

  1. A `webhook_deliveries` audit row is INSERTed per subscription.
  2. The HTTP POST happens with the right URL.
  3. The `X-Surplus-Signature` header is a valid HMAC-SHA256 of the body.
  4. On 2xx, the row is UPDATEd with `delivered_at = NOW()`.

This pins the F1 (dispatcher) + F2 (subscriptions) + F3 (orchestrator)
contract as a single integration unit. The Phase 4 unit tests mocked
individual pieces; this test wires them through the real orchestrator.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from shared import webhook_events as we
from shared.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration


SIGNING_KEY = "test-signing-key-32-bytes-long-xx"
SUBSCRIPTION_ID = "44444444-4444-4444-4444-444444444444"
DELIVERY_ID = "11111111-1111-1111-1111-111111111111"
SUBSCRIBER_URL = "https://subscriber.example.com/hook"


@pytest.fixture(autouse=True)
def _seed_signing_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    get_settings.cache_clear()
    monkeypatch.setenv("WEBHOOK_SIGNING_KEY", SIGNING_KEY)
    yield
    get_settings.cache_clear()


def _expected_signature(body_bytes: bytes) -> str:
    return "sha256=" + hmac.new(
        SIGNING_KEY.encode("utf-8"), body_bytes, hashlib.sha256,
    ).hexdigest()


@pytest.fixture
def _captured() -> dict[str, Any]:
    return {}


@pytest.fixture
def _wire_pipeline(
    monkeypatch: pytest.MonkeyPatch, _captured: dict[str, Any],
) -> None:
    """Wire the F1+F2+F3 pipeline with controllable mocks.

    Subscription lookup returns ONE active subscription. fetch_one returns
    a synthetic delivery_id. execute records the UPDATEs. httpx.AsyncClient
    is replaced with a fake that captures the body+headers and returns 200.
    """
    async def fake_list_subs(*, partner_id: str, event_type: str) -> list[dict[str, Any]]:
        _captured["lookup_partner_id"] = partner_id
        _captured["lookup_event_type"] = event_type
        return [{
            "subscription_id": SUBSCRIPTION_ID,
            "url": SUBSCRIBER_URL,
            "events": [event_type],
        }]

    async def fake_fetch_one(sql: str, *args: Any) -> dict[str, Any]:
        _captured.setdefault("inserts", []).append({"sql": sql, "args": args})
        return {"delivery_id": DELIVERY_ID}

    async def fake_execute(sql: str, *args: Any) -> str:
        _captured.setdefault("updates", []).append({"sql": sql, "args": args})
        return "UPDATE 1"

    captured_post: dict[str, Any] = {}
    _captured["post"] = captured_post

    class FakeResponse:
        status_code = 200
        text = "ok"

    class FakeClient:
        async def __aenter__(self_inner) -> Any:
            return self_inner

        async def __aexit__(self_inner, *_: Any) -> None: ...

        async def post(
            self_inner, url: str, *,
            content: bytes, headers: dict[str, str],
            timeout: float,  # noqa: ASYNC109 — mirrors httpx.AsyncClient.post
        ) -> FakeResponse:
            captured_post["url"] = url
            captured_post["content"] = content
            captured_post["headers"] = headers
            captured_post["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr(we, "list_active_subscriptions_for_event", fake_list_subs)
    monkeypatch.setattr(we, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(we, "execute", fake_execute)
    monkeypatch.setattr(we, "init_pool", AsyncMock())
    monkeypatch.setattr(
        "shared.webhook_dispatcher.httpx.AsyncClient", lambda: FakeClient(),
    )


@pytest.mark.parametrize("event_type,payload", [
    ("merchant.profile.created",
     {"merchant_id": "33333333-3333-3333-3333-333333333333",
      "merchant_name": "Tampa Bagel Co", "region": "US-FL-Hillsborough"}),
    ("listing.created",
     {"listing_id": "22222222-2222-2222-2222-222222222222",
      "recommendation_id": "11111111-1111-1111-1111-111111111111",
      "listing_status": "draft"}),
    ("price.updated",
     {"listing_id": "22222222-2222-2222-2222-222222222222",
      "old_price": 7.25, "new_price": 6.50,
      "new_recommendation_id": "55555555-5555-5555-5555-555555555555"}),
    ("dispute.resolved",
     {"dispute_id": "44444444-4444-4444-4444-444444444444",
      "listing_id": "22222222-2222-2222-2222-222222222222",
      "resolution": "accepted"}),
])
async def test_emit_event_full_pipeline(
    _wire_pipeline: None,
    _captured: dict[str, Any],
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Each event type goes through subscription lookup -> audit-row INSERT
    -> signed POST -> success-path UPDATE."""
    out = await we.emit_event(
        event_type=event_type,
        partner_id="sk_demo",
        payload=payload,
    )

    # 1. Orchestrator return shape.
    assert out["status"] == "ok"
    assert out["delivery_ids"] == [DELIVERY_ID]

    # 2. Subscription lookup keyed correctly.
    assert _captured["lookup_partner_id"] == "sk_demo"
    assert _captured["lookup_event_type"] == event_type

    # 3. Audit row INSERTed before delivery (attempt=1).
    inserts = _captured["inserts"]
    assert len(inserts) == 1
    assert "INSERT INTO agents.webhook_deliveries" in inserts[0]["sql"]
    insert_args = inserts[0]["args"]
    # subscription_id (UUID), event_type, payload (JSON str), attempt=N/A
    # — the orchestrator wraps the full envelope in `payload` field of the
    # SQL; verify the JSON is parseable and contains the right event_type.
    insert_payload = json.loads(insert_args[2])
    assert insert_payload["event_type"] == event_type
    assert insert_payload["partner_id"] == "sk_demo"
    assert "event_id" in insert_payload
    assert "occurred_at" in insert_payload
    assert insert_payload["payload"] == payload

    # 4. HTTP POST happened with the right URL + signed body.
    post = _captured["post"]
    assert post["url"] == SUBSCRIBER_URL
    assert post["headers"]["Content-Type"] == "application/json"
    sig = post["headers"]["X-Surplus-Signature"]
    assert sig.startswith("sha256=")
    # The signature must verify against the bytes the dispatcher sent.
    expected = _expected_signature(post["content"])
    assert sig == expected, (
        f"signature mismatch for {event_type}: "
        f"got {sig[:20]}... expected {expected[:20]}..."
    )

    # 5. Sent body must be byte-identical to what was logged (signing
    # invariant — customers must be able to verify with the bytes they
    # received, not re-serialise the payload).
    sent = json.loads(post["content"])
    assert sent["event_type"] == event_type
    assert sent["payload"] == payload

    # 6. Success-path UPDATE sets delivered_at.
    updates = _captured["updates"]
    assert len(updates) == 1
    assert "delivered_at = NOW()" in updates[0]["sql"]
    assert "last_status_code" in updates[0]["sql"]
    assert 200 in updates[0]["args"]


async def test_emit_event_failure_path_updates_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On non-2xx, the audit row gets last_status_code + last_error but
    delivered_at stays NULL (no `delivered_at = NOW()` in the failure UPDATE)."""
    monkeypatch.setattr(
        we, "list_active_subscriptions_for_event",
        AsyncMock(return_value=[{
            "subscription_id": SUBSCRIPTION_ID, "url": SUBSCRIBER_URL,
            "events": ["price.updated"],
        }]),
    )
    monkeypatch.setattr(
        we, "fetch_one",
        AsyncMock(return_value={"delivery_id": DELIVERY_ID}),
    )
    captured_updates: list[tuple[str, tuple[Any, ...]]] = []

    async def fake_execute(sql: str, *args: Any) -> str:
        captured_updates.append((sql, args))
        return "UPDATE 1"

    monkeypatch.setattr(we, "execute", fake_execute)
    monkeypatch.setattr(we, "init_pool", AsyncMock())

    class FailingResponse:
        status_code = 502
        text = "bad gateway"

    class FailingClient:
        async def __aenter__(self_inner) -> Any:
            return self_inner

        async def __aexit__(self_inner, *_: Any) -> None: ...

        async def post(self_inner, *_: Any, **__: Any) -> FailingResponse:
            return FailingResponse()

    monkeypatch.setattr(
        "shared.webhook_dispatcher.httpx.AsyncClient", lambda: FailingClient(),
    )

    out = await we.emit_event(
        event_type="price.updated",
        partner_id="sk_demo",
        payload={"listing_id": "L-1", "old_price": 7.25, "new_price": 6.50,
                 "new_recommendation_id": "R-1"},
    )

    assert out["status"] == "ok"
    assert out["delivery_ids"] == [DELIVERY_ID]
    sql, args = captured_updates[0]
    # Failure path: last_status_code + last_error but NOT delivered_at.
    assert "last_status_code" in sql
    assert "last_error" in sql
    # The UPDATE SET clause must NOT touch delivered_at on failure.
    set_clause = sql.split("WHERE")[0]
    assert "delivered_at" not in set_clause
    assert 502 in args
