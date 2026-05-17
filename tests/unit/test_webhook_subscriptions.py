"""Unit tests for shared.webhook_subscriptions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

if TYPE_CHECKING:
    import pytest

from shared.webhook_subscriptions import (
    create_subscription,
    deactivate_subscription,
    list_active_subscriptions_for_event,
)


async def test_create_subscription_inserts_with_hashed_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_fetch_one(sql: str, *args: Any) -> dict[str, Any]:
        captured["sql"] = sql
        captured["args"] = args
        return {"subscription_id": "s-1"}

    from shared import webhook_subscriptions as ws
    monkeypatch.setattr(ws, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(ws, "init_pool", AsyncMock())

    out = await create_subscription(
        partner_id="sk_demo",
        url="https://example.com/hook",
        events=["price.updated", "listing.created"],
        secret="a-very-long-shared-secret-here",
    )
    assert out["status"] == "ok"
    assert out["subscription_id"] == "s-1"
    # Plaintext secret must NOT appear in bound args; only the hash.
    assert "a-very-long-shared-secret-here" not in str(captured["args"])
    # Events stored as TEXT[] (passed as list, asyncpg auto-converts).
    assert captured["args"][2] == ["price.updated", "listing.created"]


async def test_create_subscription_rejects_non_https_url() -> None:
    out = await create_subscription(
        partner_id="sk_demo", url="http://insecure.com/hook",
        events=["price.updated"], secret="a-very-long-shared-secret-here",
    )
    assert out["status"] == "validation_error"
    assert out["field"] == "url"


async def test_create_subscription_rejects_empty_events_list() -> None:
    out = await create_subscription(
        partner_id="sk_demo", url="https://example.com/hook",
        events=[], secret="a-very-long-shared-secret-here",
    )
    assert out["status"] == "validation_error"
    assert out["field"] == "events"


async def test_create_subscription_rejects_short_secret() -> None:
    out = await create_subscription(
        partner_id="sk_demo", url="https://example.com/hook",
        events=["price.updated"], secret="short",
    )
    assert out["status"] == "validation_error"
    assert out["field"] == "secret"


async def test_list_active_subscriptions_filters_by_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shared import webhook_subscriptions as ws
    monkeypatch.setattr(
        ws, "fetch_all",
        AsyncMock(return_value=[
            {"subscription_id": "s-1", "url": "https://a.com",
             "events": ["price.updated"]},
        ]),
    )
    monkeypatch.setattr(ws, "init_pool", AsyncMock())

    subs = await list_active_subscriptions_for_event(
        partner_id="sk_demo", event_type="price.updated",
    )
    assert len(subs) == 1
    assert subs[0]["url"] == "https://a.com"


async def test_deactivate_subscription_sets_active_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from shared import webhook_subscriptions as ws
    monkeypatch.setattr(ws, "execute",
                         AsyncMock(return_value="UPDATE 1"))
    monkeypatch.setattr(ws, "init_pool", AsyncMock())

    out = await deactivate_subscription(
        subscription_id="44444444-4444-4444-4444-444444444444",
        partner_id="sk_demo",
    )
    assert out["status"] == "ok"


async def test_deactivate_subscription_rejects_invalid_uuid() -> None:
    out = await deactivate_subscription(
        subscription_id="not-a-uuid", partner_id="sk_demo",
    )
    assert out["status"] == "validation_error"
    assert out["field"] == "subscription_id"
