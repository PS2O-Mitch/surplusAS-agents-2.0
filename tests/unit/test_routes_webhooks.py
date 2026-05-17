"""Unit tests for service.routes_webhooks (POST/DELETE subscriptions)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from service.app import create_app
from shared.auth import PartnerContext, require_partner

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[require_partner] = lambda: PartnerContext(
        api_key="sk_test", partner_id="sk_demo", context={},
    )
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_post_subscription_creates(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_create(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"status": "ok", "subscription_id":
                "44444444-4444-4444-4444-444444444444"}

    monkeypatch.setattr("service.routes_webhooks.create_subscription",
                        fake_create)

    resp = client.post(
        "/v1/webhooks/subscriptions",
        json={"url": "https://example.com/hook",
              "events": ["price.updated"],
              "secret": "a-very-long-shared-secret-here"},
        headers={"Authorization": "Bearer sk_test"},
    )
    assert resp.status_code == 200
    assert resp.json()["subscription_id"] == \
        "44444444-4444-4444-4444-444444444444"
    # The route must scope the create to the authenticated partner_id —
    # never trust a body field for it.
    assert captured["partner_id"] == "sk_demo"
    assert captured["url"] == "https://example.com/hook"
    assert captured["events"] == ["price.updated"]
    assert captured["secret"] == "a-very-long-shared-secret-here"


def test_post_subscription_422_on_short_secret(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    async def fake_create(**_: Any) -> dict[str, Any]:
        return {"status": "validation_error",
                "error": "secret must be at least 16 chars",
                "field": "secret"}

    monkeypatch.setattr("service.routes_webhooks.create_subscription",
                        fake_create)

    resp = client.post(
        "/v1/webhooks/subscriptions",
        json={"url": "https://example.com/hook",
              "events": ["price.updated"], "secret": "short"},
        headers={"Authorization": "Bearer sk_test"},
    )
    assert resp.status_code == 422


def test_post_subscription_422_on_non_https(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    async def fake_create(**_: Any) -> dict[str, Any]:
        return {"status": "validation_error",
                "error": "url must use https://", "field": "url"}

    monkeypatch.setattr("service.routes_webhooks.create_subscription",
                        fake_create)

    resp = client.post(
        "/v1/webhooks/subscriptions",
        json={"url": "http://insecure.com/hook",
              "events": ["price.updated"],
              "secret": "a-very-long-shared-secret-here"},
        headers={"Authorization": "Bearer sk_test"},
    )
    assert resp.status_code == 422


def test_delete_subscription(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_deactivate(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"status": "ok", "rows": "UPDATE 1"}

    monkeypatch.setattr("service.routes_webhooks.deactivate_subscription",
                         fake_deactivate)

    resp = client.delete(
        "/v1/webhooks/subscriptions/44444444-4444-4444-4444-444444444444",
        headers={"Authorization": "Bearer sk_test"},
    )
    assert resp.status_code == 204
    # Scoped by authenticated partner_id (cross-tenant guard).
    assert captured["partner_id"] == "sk_demo"
    assert captured["subscription_id"] == \
        "44444444-4444-4444-4444-444444444444"


def test_delete_subscription_422_on_invalid_uuid(
    monkeypatch: pytest.MonkeyPatch, client: TestClient,
) -> None:
    async def fake_deactivate(**_: Any) -> dict[str, Any]:
        return {"status": "validation_error",
                "error": "invalid UUID 'not-a-uuid'",
                "field": "subscription_id"}

    monkeypatch.setattr("service.routes_webhooks.deactivate_subscription",
                         fake_deactivate)

    resp = client.delete(
        "/v1/webhooks/subscriptions/not-a-uuid",
        headers={"Authorization": "Bearer sk_test"},
    )
    assert resp.status_code == 422
