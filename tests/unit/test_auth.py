"""Unit tests for `shared.auth`.

`require_partner` is the FastAPI dependency every public route consumes. Tests
mock `fetch_one` so no DB is required and exercise the four user-visible
paths: happy resolve, missing header, empty token, unknown api_key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from shared import auth

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _clear_partner_cache() -> Iterator[None]:
    """Each test starts with an empty cache to avoid cross-test leakage."""
    auth._PARTNER_CACHE.clear()
    yield
    auth._PARTNER_CACHE.clear()


async def test_require_partner_resolves_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_row = {
        "api_key": "sk_demo",
        "partner_id": "demo_partner",
        "context_json": {"name": "Demo Partner"},
    }
    monkeypatch.setattr(auth, "fetch_one", AsyncMock(return_value=fake_row))

    ctx = await auth.require_partner(authorization="Bearer sk_demo")
    assert ctx.api_key == "sk_demo"
    assert ctx.partner_id == "demo_partner"
    assert ctx.context == {"name": "Demo Partner"}


async def test_require_partner_rejects_missing_bearer_prefix() -> None:
    with pytest.raises(HTTPException) as exc:
        await auth.require_partner(authorization="Token sk_x")
    assert exc.value.status_code == 401
    assert "bearer" in exc.value.detail.lower()


async def test_require_partner_rejects_empty_token() -> None:
    with pytest.raises(HTTPException) as exc:
        await auth.require_partner(authorization="Bearer   ")
    assert exc.value.status_code == 401
    assert "empty" in exc.value.detail.lower()


async def test_require_partner_rejects_unknown_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "fetch_one", AsyncMock(return_value=None))

    with pytest.raises(HTTPException) as exc:
        await auth.require_partner(authorization="Bearer sk_nonexistent")
    assert exc.value.status_code == 401
    assert "invalid" in exc.value.detail.lower()


async def test_require_partner_caches_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Second call within TTL should not hit the DB."""
    fake_fetch = AsyncMock(return_value={
        "api_key": "sk_demo", "partner_id": "demo_partner", "context_json": {},
    })
    monkeypatch.setattr(auth, "fetch_one", fake_fetch)

    await auth.require_partner(authorization="Bearer sk_demo")
    await auth.require_partner(authorization="Bearer sk_demo")

    assert fake_fetch.call_count == 1
