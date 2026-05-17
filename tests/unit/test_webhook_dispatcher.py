# tests/unit/test_webhook_dispatcher.py
"""Unit tests for shared.webhook_dispatcher.

The dispatcher signs the body, POSTs via httpx, and reports outcome.
Tests mock httpx.AsyncClient so no network access is needed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

from shared.webhook_dispatcher import compute_signature, deliver


def test_compute_signature_matches_manual_hmac() -> None:
    body = b'{"event_id":"e-1","payload":{"x":1}}'
    secret = "topsecret"
    sig = compute_signature(body, secret)
    expected = "sha256=" + hmac.new(secret.encode(), body,
                                     hashlib.sha256).hexdigest()
    assert sig == expected


async def test_deliver_posts_with_signature_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        status_code = 200
        text = "ok"

    class FakeClient:
        async def __aenter__(self_inner) -> Any: return self_inner
        async def __aexit__(self_inner, *_: Any) -> None: ...
        async def post(self_inner, url: str, *, content: bytes,
                        headers: dict[str, str], timeout: float) -> FakeResponse:  # noqa: ASYNC109
            captured["url"] = url
            captured["content"] = content
            captured["headers"] = headers
            captured["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr("shared.webhook_dispatcher.httpx.AsyncClient",
                        lambda: FakeClient())

    payload = {"event_id": "e-1", "event_type": "price.updated",
               "partner_id": "sk_demo",
               "occurred_at": "2026-05-20T00:00:00Z",
               "payload": {"x": 1}}
    out = await deliver(
        url="https://example.com/hook",
        payload=payload,
        signing_key="topsecret",
    )
    assert out["status_code"] == 200
    assert captured["url"] == "https://example.com/hook"
    assert captured["headers"]["Content-Type"] == "application/json"
    sig_header = captured["headers"]["X-Surplus-Signature"]
    assert sig_header.startswith("sha256=")
    # The signature must match the SHA-256 HMAC of the JSON body.
    expected_body = json.dumps(payload, separators=(",", ":")).encode()
    expected = "sha256=" + hmac.new(b"topsecret", expected_body,
                                     hashlib.sha256).hexdigest()
    assert sig_header == expected


async def test_deliver_returns_error_on_4xx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        status_code = 404
        text = "not found"

    class FakeClient:
        async def __aenter__(self_inner) -> Any: return self_inner
        async def __aexit__(self_inner, *_: Any) -> None: ...
        async def post(self_inner, *_: Any, **__: Any) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr("shared.webhook_dispatcher.httpx.AsyncClient",
                        lambda: FakeClient())

    out = await deliver(
        url="https://example.com/hook",
        payload={"event_id": "x"},
        signing_key="k",
    )
    assert out["status_code"] == 404
    assert "not found" in out["error"]


async def test_deliver_returns_error_on_network_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network exceptions become {status_code: 0, error: ...} — no raise."""
    class FakeClient:
        async def __aenter__(self_inner) -> Any: return self_inner
        async def __aexit__(self_inner, *_: Any) -> None: ...
        async def post(self_inner, *_: Any, **__: Any) -> Any:
            raise ConnectionError("connection refused")

    monkeypatch.setattr("shared.webhook_dispatcher.httpx.AsyncClient",
                        lambda: FakeClient())

    out = await deliver(
        url="https://example.com/hook",
        payload={"event_id": "x"},
        signing_key="k",
    )
    assert out["status_code"] == 0
    assert "ConnectionError" in out["error"] or "connection refused" in out["error"]
