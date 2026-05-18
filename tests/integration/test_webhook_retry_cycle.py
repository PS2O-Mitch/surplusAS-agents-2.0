"""End-to-end integration test: one full retry cycle.

DB is mocked (no Postgres needed); httpx is mocked (no network needed).
What this test pins:

1. The worker SELECTs candidates with the right WHERE clause (via fake_fetch_all
   capture).
2. For each candidate, it POSTs the original payload (parsed from the row)
   with a fresh signature derived from the repo-wide WEBHOOK_SIGNING_KEY.
3. On 2xx, the row is UPDATEd with attempt+1, last_attempt_at=NOW(),
   delivered_at=NOW().

This integration test exists alongside the unit tests in
`test_webhook_retry.py` to pin the full sign + POST + persist contract
when the dispatcher is wired through, rather than mocked individually.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from shared import webhook_retry as wr
from shared.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.integration

SIGNING_KEY = "test-signing-key-32-bytes-long-xx"


@pytest.fixture(autouse=True)
def _seed_signing_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    get_settings.cache_clear()
    monkeypatch.setenv("WEBHOOK_SIGNING_KEY", SIGNING_KEY)
    yield
    get_settings.cache_clear()


async def test_one_full_retry_cycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """A previously-failed row is redelivered, signed correctly, UPDATEd
    with delivered_at on a 200 response."""
    full_payload = {
        "event_id": "e-1", "event_type": "price.updated",
        "partner_id": "sk_demo",
        "occurred_at": "2026-05-17T00:00:00Z",
        "payload": {"listing_id": "L-1", "old_price": 7.25, "new_price": 6.50},
    }
    candidate_row = {
        "delivery_id": "11111111-1111-1111-1111-111111111111",
        "subscription_id": "44444444-4444-4444-4444-444444444444",
        "event_type": "price.updated",
        "payload": full_payload,  # asyncpg returns JSONB as a dict
        "attempt": 1,
        "url": "https://subscriber.example.com/hook",
    }
    monkeypatch.setattr(wr, "fetch_all",
                        AsyncMock(return_value=[candidate_row]))
    monkeypatch.setattr(wr, "init_pool", AsyncMock())

    captured_updates: list[tuple[str, tuple[Any, ...]]] = []

    async def fake_execute(sql: str, *args: Any) -> str:
        captured_updates.append((sql, args))
        return "UPDATE 1"

    monkeypatch.setattr(wr, "execute", fake_execute)

    captured_post: dict[str, Any] = {}

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
            return FakeResponse()

    monkeypatch.setattr("shared.webhook_dispatcher.httpx.AsyncClient",
                        lambda: FakeClient())

    summary = await wr.retry_failed_deliveries(limit=100)

    assert summary["scanned"] == 1
    assert summary["retried"] == 1
    assert summary["succeeded"] == 1

    # The POST URL matches the (current) subscription URL.
    assert captured_post["url"] == "https://subscriber.example.com/hook"

    # The body is the original event payload, byte-for-byte identical
    # to what was originally INSERTed.
    sent_payload = json.loads(captured_post["content"])
    assert sent_payload == full_payload

    # Signature verifies against sent bytes.
    sig = captured_post["headers"]["X-Surplus-Signature"]
    expected = "sha256=" + hmac.new(
        SIGNING_KEY.encode("utf-8"), captured_post["content"],
        hashlib.sha256,
    ).hexdigest()
    assert sig == expected

    # UPDATE: attempt += 1, last_attempt_at = NOW(), delivered_at = NOW().
    sql, _args = captured_updates[0]
    assert "attempt = attempt + 1" in sql
    assert "last_attempt_at = NOW()" in sql
    assert "delivered_at = NOW()" in sql


async def test_full_retry_cycle_with_string_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When asyncpg returns the JSONB column as a string (no codec registered),
    the worker still parses + signs the body correctly."""
    full_payload = {
        "event_id": "e-2", "event_type": "listing.created",
        "partner_id": "sk_demo",
        "occurred_at": "2026-05-17T00:00:00Z",
        "payload": {"listing_id": "L-2"},
    }
    candidate_row = {
        "delivery_id": "22222222-2222-2222-2222-222222222222",
        "subscription_id": "44444444-4444-4444-4444-444444444444",
        "event_type": "listing.created",
        "payload": json.dumps(full_payload),  # stored as string
        "attempt": 2,
        "url": "https://subscriber.example.com/hook",
    }
    monkeypatch.setattr(wr, "fetch_all",
                        AsyncMock(return_value=[candidate_row]))
    monkeypatch.setattr(wr, "init_pool", AsyncMock())
    monkeypatch.setattr(wr, "execute", AsyncMock(return_value="UPDATE 1"))

    captured_post: dict[str, Any] = {}

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
            timeout: float,  # noqa: ASYNC109
        ) -> FakeResponse:
            captured_post["content"] = content
            captured_post["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("shared.webhook_dispatcher.httpx.AsyncClient",
                        lambda: FakeClient())

    summary = await wr.retry_failed_deliveries(limit=100)
    assert summary["succeeded"] == 1

    # The dispatcher must have received the parsed dict, not a string.
    sent_payload = json.loads(captured_post["content"])
    assert sent_payload == full_payload
