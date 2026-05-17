"""Unit tests for `agents.onboarding.tools`.

The tools layer is the only writer to `agents.merchant_profiles`, so the
contract we lock down is:

1. Validation rejects bad inputs BEFORE hitting the DB (clean error string
   the LLM can recover from).
2. The INSERT carries every field through, with the right defaults.
3. UPDATEs report not-found cleanly when the row is missing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import uuid4

from agents.onboarding import tools

if TYPE_CHECKING:
    import pytest


def _ok_kwargs(**overrides: Any) -> dict[str, Any]:
    """Default valid kwargs for create_merchant_profile."""
    base: dict[str, Any] = {
        "partner_id": "sk_demo_surplus_2026",
        "merchant_name": "Tampa Bagel Co",
        "region": "US-FL-Hillsborough",
        "allowed_categories": ["bakery", "prepared_meal"],
        "merchant_floor_pct": 0.10,
        "timezone": "America/New_York",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# create_merchant_profile
# ---------------------------------------------------------------------------


async def test_create_merchant_profile_inserts_and_returns_uuid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    new_id = uuid4()
    fetch_one_mock = AsyncMock(
        return_value={
            "merchant_id": new_id,
            "created_at": datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        }
    )
    emit_mock = AsyncMock(return_value={"status": "ok", "delivery_ids": []})
    monkeypatch.setattr(tools, "fetch_one", fetch_one_mock)
    monkeypatch.setattr(tools, "emit_event", emit_mock)

    out = await tools.create_merchant_profile(**_ok_kwargs())

    assert out["status"] == "ok"
    assert out["merchant_id"] == str(new_id)
    assert out["allowed_categories"] == ["bakery", "prepared_meal"]
    assert out["merchant_floor_pct"] == 0.10
    fetch_one_mock.assert_awaited_once()
    sql, *args = fetch_one_mock.await_args.args
    assert "INSERT INTO agents.merchant_profiles" in sql
    assert args == [
        "sk_demo_surplus_2026",
        "Tampa Bagel Co",
        "US-FL-Hillsborough",
        0.10,
        ["bakery", "prepared_meal"],
        "America/New_York",
    ]


async def test_create_merchant_profile_rejects_invalid_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_one_mock = AsyncMock()
    monkeypatch.setattr(tools, "fetch_one", fetch_one_mock)

    out = await tools.create_merchant_profile(
        **_ok_kwargs(allowed_categories=["sandwiches"])
    )

    assert out["status"] == "validation_error"
    assert out["field"] == "allowed_categories"
    assert "sandwiches" in out["error"]
    fetch_one_mock.assert_not_awaited()


async def test_create_merchant_profile_rejects_empty_category_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_one_mock = AsyncMock()
    monkeypatch.setattr(tools, "fetch_one", fetch_one_mock)

    out = await tools.create_merchant_profile(**_ok_kwargs(allowed_categories=[]))

    assert out["status"] == "validation_error"
    assert out["field"] == "allowed_categories"
    fetch_one_mock.assert_not_awaited()


async def test_create_merchant_profile_rejects_floor_above_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch_one_mock = AsyncMock()
    monkeypatch.setattr(tools, "fetch_one", fetch_one_mock)

    out = await tools.create_merchant_profile(**_ok_kwargs(merchant_floor_pct=1.5))

    assert out["status"] == "validation_error"
    assert out["field"] == "merchant_floor_pct"
    fetch_one_mock.assert_not_awaited()


async def test_create_merchant_profile_rejects_negative_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools, "fetch_one", AsyncMock())
    out = await tools.create_merchant_profile(**_ok_kwargs(merchant_floor_pct=-0.05))
    assert out["status"] == "validation_error"
    assert out["field"] == "merchant_floor_pct"


async def test_create_merchant_profile_rejects_blank_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools, "fetch_one", AsyncMock())
    out = await tools.create_merchant_profile(**_ok_kwargs(region="   "))
    assert out["status"] == "validation_error"
    assert out["field"] == "region"


# ---------------------------------------------------------------------------
# set_floor_pct
# ---------------------------------------------------------------------------


async def test_set_floor_pct_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_mock = AsyncMock(return_value="UPDATE 1")
    monkeypatch.setattr(tools, "execute", execute_mock)
    mid = str(uuid4())
    out = await tools.set_floor_pct(mid, 0.20)
    assert out == {"status": "ok", "merchant_id": mid, "merchant_floor_pct": 0.20}
    execute_mock.assert_awaited_once()


async def test_set_floor_pct_rejects_bad_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    execute_mock = AsyncMock()
    monkeypatch.setattr(tools, "execute", execute_mock)
    out = await tools.set_floor_pct("not-a-uuid", 0.20)
    assert out["status"] == "validation_error"
    assert out["field"] == "merchant_id"
    execute_mock.assert_not_awaited()


async def test_set_floor_pct_rejects_out_of_range(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "execute", AsyncMock())
    out = await tools.set_floor_pct(str(uuid4()), 1.10)
    assert out["status"] == "validation_error"
    assert out["field"] == "merchant_floor_pct"


async def test_set_floor_pct_returns_error_when_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools, "execute", AsyncMock(return_value="UPDATE 0"))
    out = await tools.set_floor_pct(str(uuid4()), 0.15)
    assert out["status"] == "validation_error"
    assert "not found" in out["error"]


# ---------------------------------------------------------------------------
# set_categories
# ---------------------------------------------------------------------------


async def test_set_categories_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "execute", AsyncMock(return_value="UPDATE 1"))
    mid = str(uuid4())
    out = await tools.set_categories(mid, ["bakery", "deli"])
    assert out == {
        "status": "ok",
        "merchant_id": mid,
        "allowed_categories": ["bakery", "deli"],
    }


async def test_set_categories_rejects_unknown_category(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute_mock = AsyncMock()
    monkeypatch.setattr(tools, "execute", execute_mock)
    out = await tools.set_categories(str(uuid4()), ["wings"])
    assert out["status"] == "validation_error"
    assert out["field"] == "allowed_categories"
    execute_mock.assert_not_awaited()


async def test_set_categories_rejects_bad_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "execute", AsyncMock())
    out = await tools.set_categories("nope", ["bakery"])
    assert out["status"] == "validation_error"
    assert out["field"] == "merchant_id"


async def test_set_categories_returns_error_when_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools, "execute", AsyncMock(return_value="UPDATE 0"))
    out = await tools.set_categories(str(uuid4()), ["bakery"])
    assert out["status"] == "validation_error"
    assert "not found" in out["error"]


# ---------------------------------------------------------------------------
# set_region
# ---------------------------------------------------------------------------


async def test_set_region_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "execute", AsyncMock(return_value="UPDATE 1"))
    mid = str(uuid4())
    out = await tools.set_region(mid, "US-NY-Kings")
    assert out == {"status": "ok", "merchant_id": mid, "region": "US-NY-Kings"}


async def test_set_region_rejects_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "execute", AsyncMock())
    out = await tools.set_region(str(uuid4()), "")
    assert out["status"] == "validation_error"
    assert out["field"] == "region"


async def test_set_region_rejects_bad_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "execute", AsyncMock())
    out = await tools.set_region("bogus", "US-FL")
    assert out["status"] == "validation_error"
    assert out["field"] == "merchant_id"


async def test_set_region_returns_error_when_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools, "execute", AsyncMock(return_value="UPDATE 0"))
    out = await tools.set_region(str(uuid4()), "US-FL")
    assert out["status"] == "validation_error"
    assert "not found" in out["error"]


# ---------------------------------------------------------------------------
# create_merchant_profile — webhook emission
# ---------------------------------------------------------------------------


async def test_create_merchant_profile_emits_webhook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a successful INSERT, emit `merchant.profile.created`."""
    from agents.onboarding import tools as ob

    captured: dict[str, Any] = {}

    async def fake_emit_event(*, event_type: str, partner_id: str,
                               payload: dict[str, Any]) -> dict[str, Any]:
        captured["event_type"] = event_type
        captured["partner_id"] = partner_id
        captured["payload"] = payload
        return {"status": "ok", "delivery_ids": ["d-1"]}

    async def fake_fetch_one(sql: str, *args: Any) -> dict[str, Any]:
        import datetime as dt
        return {"merchant_id":
                "00000000-0000-0000-0000-000000000001",
                "created_at": dt.datetime(2026, 5, 17, 12, 0, 0)}

    monkeypatch.setattr(ob, "emit_event", fake_emit_event)
    monkeypatch.setattr(ob, "fetch_one", fake_fetch_one)

    out = await ob.create_merchant_profile(
        partner_id="sk_demo",
        merchant_name="Tampa Bagel Co",
        region="US-FL-Hillsborough",
        allowed_categories=["bakery", "prepared_meal"],
        merchant_floor_pct=0.12,
    )
    assert out["status"] == "ok"
    assert out["webhook_status"] == "ok"
    assert out["webhook_delivery_ids"] == ["d-1"]
    assert captured["event_type"] == "merchant.profile.created"
    assert captured["partner_id"] == "sk_demo"
    assert captured["payload"]["merchant_id"] == \
        "00000000-0000-0000-0000-000000000001"
    assert captured["payload"]["merchant_name"] == "Tampa Bagel Co"
    assert captured["payload"]["region"] == "US-FL-Hillsborough"
    assert captured["payload"]["allowed_categories"] == \
        ["bakery", "prepared_meal"]


async def test_create_merchant_profile_succeeds_when_emit_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If emit_event raises, the primary write must still succeed —
    the audit row is the source of truth."""
    from agents.onboarding import tools as ob

    async def fake_emit_event(**_: Any) -> dict[str, Any]:
        raise RuntimeError("subscriptions DB unreachable")

    async def fake_fetch_one(sql: str, *args: Any) -> dict[str, Any]:
        import datetime as dt
        return {"merchant_id":
                "00000000-0000-0000-0000-000000000001",
                "created_at": dt.datetime(2026, 5, 17, 12, 0, 0)}

    monkeypatch.setattr(ob, "emit_event", fake_emit_event)
    monkeypatch.setattr(ob, "fetch_one", fake_fetch_one)

    out = await ob.create_merchant_profile(
        partner_id="sk_demo",
        merchant_name="x",
        region="US-FL",
        allowed_categories=["bakery"],
    )
    assert out["status"] == "ok"
    assert out["webhook_status"] == "error"
    assert "subscriptions DB unreachable" in out.get("webhook_error", "")
