"""Unit tests for `agents.pricing.engine_adapter`.

The adapter is the only path that writes `agents.recommendation_log`, so
we lock its behaviour down to 100% coverage. Tests mock the pricing
engine, the asyncpg pool, and capture every INSERT — we assert the bound
parameters round-trip the audit fields the consuming repo relies on.

What we do NOT test here (deferred to integration):
- The actual SQL → Postgres types coercion (asyncpg + the schema).
- The pricing engine's math (covered upstream in surplusas-pricing).

Skips entirely when the `vendor/surplusas-pricing/` submodule isn't
initialised — the adapter transitively imports `pricing_engine.*` types
that this module also re-imports, and CI runs without the submodule
until the cross-repo PAT is provisioned (see `.github/workflows/ci.yml`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from shared import pricing_intel as _pricing_intel

if not _pricing_intel.__all__:
    pytest.skip(
        "vendor/surplusas-pricing submodule not initialised — "
        "adapter tests need the engine types. "
        "Run `git submodule update --init --recursive` to enable.",
        allow_module_level=True,
    )

from agents.pricing import engine_adapter  # noqa: E402  — gated by skip above
from shared.pricing_intel import (  # noqa: E402
    AppliedPressures,
    Coefficients,
    PiecewiseCurve,
    PricingInput,
    Recommendation,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_pricing_input(**overrides: Any) -> PricingInput:
    base: dict[str, Any] = {
        "category": "prepared_meal",
        "region": "US-FL-Hillsborough",
        "units": 1,
        "retail_value": 12.0,
        "hours_until_expiry": 4.0,
        "now_hour": 18,
        "merchant_floor_pct": 0.10,
    }
    base.update(overrides)
    return PricingInput(**base)


def _make_recommendation(price: float = 7.25) -> Recommendation:
    return Recommendation(
        recommended_price=price,
        recommended_discount_pct=0.40,
        anchor_p50=11.50,
        anchor_source="apify",
        anchor_region="US-FL",
        applied_pressures=AppliedPressures(
            base=0.10,
            expiry=0.30,
            inventory=0.05,
            time_of_day=0.05,
            merchant_floor=0.10,
            clamped_to_floor=False,
            clamped_to_retail=False,
        ),
        formula_version="v1",
    )


def _make_coefficients(version: int = 7) -> Coefficients:
    flat = PiecewiseCurve(breakpoints=[(0.0, 0.0), (1.0, 0.0)])
    return Coefficients(
        category="prepared_meal",
        region="US-FL",
        version=version,
        base_discount=0.10,
        expiry_curve=flat,
        inventory_curve=flat,
        time_of_day_curve=flat,
        source="seed-v1",
    )


def _make_anchor() -> Any:
    """Anchor return shape from `pricing_engine.anchors.lookup_anchor`."""
    a = MagicMock()
    a.p50 = 11.50
    a.source = "apify"
    a.region = "US-FL"
    return a


class _CapturingConn:
    """Async-context-manager-compatible asyncpg connection stand-in.

    Holds the captured INSERT params so tests can assert on what was
    written. `fetchrow` returns either the seed row (for replay reads)
    or a freshly-constructed RETURNING row (for INSERTs), based on the
    SQL's leading verb.
    """

    def __init__(self, *, seed_row: dict[str, Any] | None = None) -> None:
        self.seed_row = seed_row
        self.captured_inserts: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchrow_calls: list[tuple[str, tuple[Any, ...]]] = []
        self.next_recommendation_id = uuid4()
        self.next_created_at = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.fetchrow_calls.append((sql, args))
        if sql.strip().upper().startswith("INSERT"):
            self.captured_inserts.append((sql, args))
            return {
                "recommendation_id": self.next_recommendation_id,
                "created_at": self.next_created_at,
            }
        # SELECT path — replay reads use this.
        return self.seed_row


class _FakePool:
    """`pool.acquire()` async-context-manager that yields our capturing conn."""

    def __init__(self, conn: _CapturingConn) -> None:
        self._conn = conn

    def acquire(self) -> _PoolAcquireCtx:
        return _PoolAcquireCtx(self._conn)


class _PoolAcquireCtx:
    def __init__(self, conn: _CapturingConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _CapturingConn:
        return self._conn

    async def __aexit__(self, *_: Any) -> None:
        return None


@pytest.fixture
def fake_conn() -> _CapturingConn:
    return _CapturingConn()


@pytest.fixture(autouse=True)
def _patch_pool(monkeypatch: pytest.MonkeyPatch, fake_conn: _CapturingConn) -> None:
    """Replace `init_pool` so tests never touch real DB infra.

    `init_pool` is async in `shared.db`, so the patch must return an
    awaitable that yields the fake pool. AsyncMock makes the call
    `await init_pool()` resolve to `_FakePool(fake_conn)`.
    """
    monkeypatch.setattr(
        engine_adapter,
        "init_pool",
        AsyncMock(return_value=_FakePool(fake_conn)),
    )


# ---------------------------------------------------------------------------
# price_listing
# ---------------------------------------------------------------------------


async def test_price_listing_happy_path_inserts_one_row_with_full_audit_trail(
    monkeypatch: pytest.MonkeyPatch, fake_conn: _CapturingConn
) -> None:
    coeffs = _make_coefficients(version=7)
    rec = _make_recommendation(price=7.25)

    monkeypatch.setattr(
        engine_adapter, "lookup_anchor", _async_return(_make_anchor())
    )
    monkeypatch.setattr(engine_adapter, "load_latest", _async_return(coeffs))
    monkeypatch.setattr(engine_adapter, "recommend", lambda **_: rec)

    listing_id = uuid4()
    merchant_id = uuid4()

    entry = await engine_adapter.price_listing(
        _make_pricing_input(),
        partner_id="sk_demo_surplus_2026",
        listing_id=listing_id,
        merchant_id=merchant_id,
    )

    assert len(fake_conn.captured_inserts) == 1
    sql, args = fake_conn.captured_inserts[0]
    # Argument order from _INSERT_SQL must match.
    (
        arg_listing_id,
        arg_merchant_id,
        arg_partner_id,
        arg_pricing_input_json,
        arg_price,
        arg_discount_pct,
        arg_anchor_p50,
        arg_anchor_source,
        arg_anchor_region,
        arg_pressures_json,
        arg_formula_version,
        arg_coefficients_version,
        arg_replay_of,
    ) = args

    assert arg_listing_id == listing_id
    assert arg_merchant_id == merchant_id
    assert arg_partner_id == "sk_demo_surplus_2026"
    assert json.loads(arg_pricing_input_json)["category"] == "prepared_meal"
    assert arg_price == 7.25
    assert arg_anchor_p50 == 11.50
    assert arg_anchor_source == "apify"
    assert arg_anchor_region == "US-FL"
    assert arg_formula_version == "v1"
    assert arg_coefficients_version == 7
    assert arg_replay_of is None

    pressures = json.loads(arg_pressures_json)
    assert pressures["base"] == 0.10
    assert pressures["expiry"] == 0.30
    assert pressures["clamped_to_floor"] is False

    # Returned entry mirrors what was written.
    assert entry.recommendation_id == fake_conn.next_recommendation_id
    assert float(entry.recommended_price) == 7.25
    assert entry.formula_version == "v1"
    assert entry.coefficients_version == 7
    assert entry.replay_of is None
    assert entry.applied_pressures["expiry"] == 0.30
    # Bool flags must survive the entry boundary as bools, not be
    # coerced to 0.0/1.0 — guardrail #2 ("verbatim round-trip"). See H1
    # in the Phase 2 review.
    assert entry.applied_pressures["clamped_to_floor"] is False
    assert entry.applied_pressures["clamped_to_retail"] is False


async def test_price_listing_raises_no_anchor_when_lookup_returns_none(
    monkeypatch: pytest.MonkeyPatch, fake_conn: _CapturingConn
) -> None:
    monkeypatch.setattr(engine_adapter, "lookup_anchor", _async_return(None))
    # load_latest must not be called when there is no anchor.
    monkeypatch.setattr(
        engine_adapter,
        "load_latest",
        _async_raise(AssertionError("must not be called when anchor is missing")),
    )

    with pytest.raises(engine_adapter.NoAnchorError, match="prepared_meal"):
        await engine_adapter.price_listing(
            _make_pricing_input(), partner_id="sk_demo"
        )

    assert fake_conn.captured_inserts == []


async def test_price_listing_raises_no_coefficients_when_load_returns_none(
    monkeypatch: pytest.MonkeyPatch, fake_conn: _CapturingConn
) -> None:
    monkeypatch.setattr(
        engine_adapter, "lookup_anchor", _async_return(_make_anchor())
    )
    monkeypatch.setattr(engine_adapter, "load_latest", _async_return(None))

    with pytest.raises(engine_adapter.NoCoefficientsError, match="prepared_meal"):
        await engine_adapter.price_listing(
            _make_pricing_input(), partner_id="sk_demo"
        )

    assert fake_conn.captured_inserts == []


async def test_price_listing_with_no_listing_passes_nulls_through(
    monkeypatch: pytest.MonkeyPatch, fake_conn: _CapturingConn
) -> None:
    """Lateral A2A from Concierge can call without a listing_id (anchor probe)."""
    monkeypatch.setattr(
        engine_adapter, "lookup_anchor", _async_return(_make_anchor())
    )
    monkeypatch.setattr(
        engine_adapter, "load_latest", _async_return(_make_coefficients())
    )
    monkeypatch.setattr(engine_adapter, "recommend", lambda **_: _make_recommendation())

    entry = await engine_adapter.price_listing(
        _make_pricing_input(), partner_id="sk_demo"
    )

    _, args = fake_conn.captured_inserts[0]
    assert args[0] is None  # listing_id
    assert args[1] is None  # merchant_id
    assert entry.listing_id is None
    assert entry.merchant_id is None


# ---------------------------------------------------------------------------
# replay_recommendation
# ---------------------------------------------------------------------------


async def test_replay_recommendation_writes_new_row_with_replay_of_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_id = uuid4()
    listing_id = uuid4()
    merchant_id = uuid4()
    pricing_input_dict = _make_pricing_input().model_dump(mode="json")

    seed = {
        "listing_id": listing_id,
        "merchant_id": merchant_id,
        "partner_id": "sk_demo_surplus_2026",
        "pricing_input": pricing_input_dict,  # asyncpg returns JSONB as dict
    }
    fake_conn = _CapturingConn(seed_row=seed)
    monkeypatch.setattr(
        engine_adapter, "init_pool", AsyncMock(return_value=_FakePool(fake_conn))
    )
    monkeypatch.setattr(
        engine_adapter, "lookup_anchor", _async_return(_make_anchor())
    )
    monkeypatch.setattr(
        engine_adapter, "load_latest", _async_return(_make_coefficients(version=8))
    )
    new_rec = _make_recommendation(price=6.50)
    monkeypatch.setattr(engine_adapter, "recommend", lambda **_: new_rec)

    entry = await engine_adapter.replay_recommendation(original_id)

    # Two fetchrow calls: SELECT then INSERT.
    assert len(fake_conn.fetchrow_calls) == 2
    select_sql, select_args = fake_conn.fetchrow_calls[0]
    assert select_sql.strip().upper().startswith("SELECT")
    assert select_args == (original_id,)

    assert len(fake_conn.captured_inserts) == 1
    _, args = fake_conn.captured_inserts[0]
    assert args[0] == listing_id  # carries forward the original listing
    assert args[1] == merchant_id
    assert args[2] == "sk_demo_surplus_2026"
    assert args[4] == 6.50  # new price under fresh coefficients
    assert args[11] == 8  # fresh coefficients_version
    assert args[12] == original_id  # replay_of

    assert entry.replay_of == original_id
    assert float(entry.recommended_price) == 6.50
    assert entry.coefficients_version == 8


async def test_replay_recommendation_handles_jsonb_returned_as_str(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Some asyncpg/Postgres setups return JSONB as a JSON string."""
    original_id = uuid4()
    pricing_input_str = _make_pricing_input().model_dump_json()
    seed = {
        "listing_id": None,
        "merchant_id": None,
        "partner_id": "sk_demo",
        "pricing_input": pricing_input_str,
    }
    fake_conn = _CapturingConn(seed_row=seed)
    monkeypatch.setattr(
        engine_adapter, "init_pool", AsyncMock(return_value=_FakePool(fake_conn))
    )
    monkeypatch.setattr(
        engine_adapter, "lookup_anchor", _async_return(_make_anchor())
    )
    monkeypatch.setattr(
        engine_adapter, "load_latest", _async_return(_make_coefficients())
    )
    monkeypatch.setattr(
        engine_adapter, "recommend", lambda **_: _make_recommendation()
    )

    entry = await engine_adapter.replay_recommendation(original_id)
    assert entry.replay_of == original_id


async def test_replay_recommendation_raises_when_original_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_conn = _CapturingConn(seed_row=None)  # SELECT returns nothing
    monkeypatch.setattr(
        engine_adapter, "init_pool", AsyncMock(return_value=_FakePool(fake_conn))
    )

    missing_id = uuid4()
    with pytest.raises(engine_adapter.RecommendationNotFoundError):
        await engine_adapter.replay_recommendation(missing_id)


async def test_replay_recommendation_propagates_no_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the engine has no anchor at replay time, surface it cleanly."""
    seed = {
        "listing_id": None,
        "merchant_id": None,
        "partner_id": "sk_demo",
        "pricing_input": _make_pricing_input().model_dump(mode="json"),
    }
    fake_conn = _CapturingConn(seed_row=seed)
    monkeypatch.setattr(
        engine_adapter, "init_pool", AsyncMock(return_value=_FakePool(fake_conn))
    )
    monkeypatch.setattr(engine_adapter, "lookup_anchor", _async_return(None))

    with pytest.raises(engine_adapter.NoAnchorError):
        await engine_adapter.replay_recommendation(uuid4())


async def test_replay_recommendation_propagates_no_coefficients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = {
        "listing_id": None,
        "merchant_id": None,
        "partner_id": "sk_demo",
        "pricing_input": _make_pricing_input().model_dump(mode="json"),
    }
    fake_conn = _CapturingConn(seed_row=seed)
    monkeypatch.setattr(
        engine_adapter, "init_pool", AsyncMock(return_value=_FakePool(fake_conn))
    )
    monkeypatch.setattr(
        engine_adapter, "lookup_anchor", _async_return(_make_anchor())
    )
    monkeypatch.setattr(engine_adapter, "load_latest", _async_return(None))

    with pytest.raises(engine_adapter.NoCoefficientsError):
        await engine_adapter.replay_recommendation(uuid4())


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _async_return(value: Any) -> Any:
    async def _f(*_args: Any, **_kwargs: Any) -> Any:
        return value

    return _f


def _async_raise(exc: BaseException) -> Any:
    async def _f(*_args: Any, **_kwargs: Any) -> Any:
        raise exc

    return _f


# Sanity that ID type round-trips Pydantic on the entry.
async def test_round_trip_uuid_returns_uuid_type(
    monkeypatch: pytest.MonkeyPatch, fake_conn: _CapturingConn
) -> None:
    monkeypatch.setattr(
        engine_adapter, "lookup_anchor", _async_return(_make_anchor())
    )
    monkeypatch.setattr(
        engine_adapter, "load_latest", _async_return(_make_coefficients())
    )
    monkeypatch.setattr(engine_adapter, "recommend", lambda **_: _make_recommendation())

    entry = await engine_adapter.price_listing(
        _make_pricing_input(), partner_id="sk_demo"
    )
    assert isinstance(entry.recommendation_id, UUID)
