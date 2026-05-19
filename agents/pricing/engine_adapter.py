"""Deterministic pricing adapter — the only writer to `agents.recommendation_log`.

This module is the single seam between the LLM-driven Pricing agent and the
deterministic `pricing_engine`. The agent's prompt + tools call into here;
the LLM never invents a number (CLAUDE.md guardrail #1). Every successful
call appends one row to `agents.recommendation_log` (guardrail #3 —
append-only; replays write a NEW row with `replay_of` set).

Two entry points:

- `price_listing(pricing_input, partner_id, …)` — Listing Intake's
  lateral A2A path. Resolves coefficients + anchor, runs the formula,
  inserts the log row, returns the entry.
- `replay_recommendation(recommendation_id)` — Dispute Triage's lateral
  A2A path. Reads the original row, re-runs the formula with FRESH
  coefficients + anchor (so coefficient updates re-derive the price),
  inserts a new row with `replay_of=<orig_id>`, returns the entry.

The engine functions take a raw asyncpg connection and a dialect literal,
because the engine is shared with `surplusAS-pricing-intel`'s ingest jobs
which run against either Postgres or local SQLite. In this service we
always use the Cloud SQL Postgres pool from `shared/db.py`, so dialect is
hard-coded to `"postgres"`.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from shared.db import init_pool
from shared.pricing_intel import (
    PricingInput,
    load_latest,
    lookup_anchor,
    recommend,
)
from shared.schemas import RecommendationLogEntry

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg

logger = logging.getLogger("surplusas.pricing.engine_adapter")


class NoAnchorError(RuntimeError):
    """Raised when no anchor row exists for (category, region) after fallback.

    Listing Intake catches this and persists the listing with
    `status='draft_no_price'`; the merchant is told the listing is parked
    until a reference price arrives.
    """


class NoCoefficientsError(RuntimeError):
    """Raised when no `pricing_coefficients` row exists for (category, region).

    Should be impossible in production once the seed job has run for every
    category — surfaces as a 500 to the gateway and a paged alert.
    """


class RecommendationNotFoundError(LookupError):
    """Raised by `replay_recommendation` when the source row does not exist."""


_INSERT_SQL = """
INSERT INTO agents.recommendation_log (
    listing_id, merchant_id, partner_id,
    pricing_input, recommended_price, recommended_discount_pct,
    anchor_p50, anchor_source, anchor_region,
    applied_pressures, formula_version, coefficients_version,
    replay_of
) VALUES (
    $1, $2, $3,
    $4::jsonb, $5, $6,
    $7, $8, $9,
    $10::jsonb, $11, $12,
    $13
)
RETURNING recommendation_id, created_at
"""


async def _resolve_and_recommend(
    conn: asyncpg.Connection,
    pricing_input: PricingInput,
) -> tuple[float, str, str, dict[str, float | bool], str, int, float, float]:
    """Look up anchor + coefficients, run the formula, return the persistable shape.

    Returns a tuple of:
        (recommended_price, anchor_source, anchor_region,
         applied_pressures_dict, formula_version, coefficients_version,
         anchor_p50, recommended_discount_pct)

    Both lookups raise the typed errors above so the caller can map them
    to user-visible behaviour (no_anchor → draft_no_price, no_coefficients
    → 500). The pricing engine itself is pure-Python and always succeeds
    once both inputs resolve.
    """
    anchor = await lookup_anchor(
        conn,
        "postgres",
        category=pricing_input.category,
        region=pricing_input.region,
    )
    if anchor is None:
        raise NoAnchorError(
            f"No anchor for category={pricing_input.category!r} "
            f"region={pricing_input.region!r}"
        )

    coeffs = await load_latest(
        conn,
        "postgres",
        category=pricing_input.category,
        region=pricing_input.region,
    )
    if coeffs is None:
        raise NoCoefficientsError(
            f"No coefficients for category={pricing_input.category!r} "
            f"region={pricing_input.region!r}"
        )

    rec = recommend(
        inp=pricing_input,
        coeffs=coeffs,
        anchor_p50=anchor.p50,
        anchor_source=anchor.source,
        anchor_region=anchor.region,
    )

    return (
        rec.recommended_price,
        rec.anchor_source,
        rec.anchor_region,
        rec.applied_pressures.model_dump(),
        rec.formula_version,
        coeffs.version,
        rec.anchor_p50,
        rec.recommended_discount_pct,
    )


def _row_to_entry(
    row: dict[str, object],
    *,
    listing_id: UUID | None,
    merchant_id: UUID | None,
    partner_id: str,
    pricing_input: PricingInput,
    recommended_price: float,
    recommended_discount_pct: float,
    anchor_p50: float,
    anchor_source: str,
    anchor_region: str,
    applied_pressures: dict[str, float | bool],
    formula_version: str,
    coefficients_version: int,
    replay_of: UUID | None,
) -> RecommendationLogEntry:
    """Project the RETURNING row + the supplied fields into a Pydantic entry."""
    # asyncpg returns floats from NUMERIC; pydantic Decimal coercion handles
    # the conversion in `RecommendationLogEntry.model_validate`.
    return RecommendationLogEntry.model_validate(
        {
            "recommendation_id": row["recommendation_id"],
            "listing_id": listing_id,
            "merchant_id": merchant_id,
            "partner_id": partner_id,
            "pricing_input": pricing_input.model_dump(mode="json"),
            "recommended_price": recommended_price,
            "recommended_discount_pct": recommended_discount_pct,
            "anchor_p50": anchor_p50,
            "anchor_source": anchor_source,
            "anchor_region": anchor_region,
            "applied_pressures": applied_pressures,
            "formula_version": formula_version,
            "coefficients_version": coefficients_version,
            "replay_of": replay_of,
            "created_at": row["created_at"],
        }
    )


async def price_listing(
    pricing_input: PricingInput,
    partner_id: str,
    *,
    listing_id: UUID | None = None,
    merchant_id: UUID | None = None,
) -> RecommendationLogEntry:
    """Compute a recommendation and append it to `agents.recommendation_log`.

    The first call from a Listing Intake draft sets `replay_of=NULL`; the
    inserted row's id becomes the listing's `initial_recommendation_id` /
    `current_recommendation_id`.
    """
    pool = await init_pool()
    async with pool.acquire() as conn:
        (
            price,
            anchor_source,
            anchor_region,
            pressures,
            formula_version,
            coefficients_version,
            anchor_p50,
            discount_pct,
        ) = await _resolve_and_recommend(conn, pricing_input)

        row = await conn.fetchrow(
            _INSERT_SQL,
            listing_id,
            merchant_id,
            partner_id,
            pricing_input.model_dump_json(),
            price,
            discount_pct,
            anchor_p50,
            anchor_source,
            anchor_region,
            json.dumps(pressures),
            formula_version,
            coefficients_version,
            None,
        )

    assert row is not None, "RETURNING is guaranteed to produce one row"
    logger.info(
        "pricing.recommendation_logged",
        extra={
            "recommendation_id": str(row["recommendation_id"]),
            "partner_id": partner_id,
            "listing_id": str(listing_id) if listing_id else None,
            "category": pricing_input.category,
            "formula_version": formula_version,
            "coefficients_version": coefficients_version,
        },
    )
    return _row_to_entry(
        dict(row),
        listing_id=listing_id,
        merchant_id=merchant_id,
        partner_id=partner_id,
        pricing_input=pricing_input,
        recommended_price=price,
        recommended_discount_pct=discount_pct,
        anchor_p50=anchor_p50,
        anchor_source=anchor_source,
        anchor_region=anchor_region,
        applied_pressures=pressures,
        formula_version=formula_version,
        coefficients_version=coefficients_version,
        replay_of=None,
    )


async def replay_recommendation(recommendation_id: UUID) -> RecommendationLogEntry:
    """Re-derive a price from a prior log row and append a NEW row.

    Reads the original row, deserialises its `pricing_input` JSONB, runs
    the engine with whatever coefficients + anchors are current at replay
    time, inserts a new row with `replay_of=<recommendation_id>`. The
    original row is never UPDATEd (guardrail #3).

    Concurrency: this is the SELECT-then-INSERT path Dispute Triage drives.
    We rely on the engine being deterministic for a given (pricing_input,
    coefficients_version, anchor) tuple, so two concurrent replays of the
    same `recommendation_id` produce two byte-identical new rows with
    different `recommendation_id`s — extra audit, not a data race. We do
    NOT take an advisory lock; if a future change makes the formula
    non-deterministic OR adds an UPDATE on the original row, revisit and
    add a `pg_advisory_xact_lock(hashtext(recommendation_id::text))` here.
    """
    pool = await init_pool()
    async with pool.acquire() as conn:
        original = await conn.fetchrow(
            "SELECT listing_id, merchant_id, partner_id, pricing_input "
            "FROM agents.recommendation_log WHERE recommendation_id = $1",
            recommendation_id,
        )
        if original is None:
            raise RecommendationNotFoundError(
                f"recommendation_id {recommendation_id!s} not found"
            )

        # JSONB → dict (asyncpg) → Pydantic for validation. If the column was
        # written by us it round-trips; if it was hand-edited the validator
        # surfaces the error before we waste a formula run.
        raw_input = original["pricing_input"]
        if isinstance(raw_input, str):
            raw_input = json.loads(raw_input)
        pricing_input = PricingInput.model_validate(raw_input)

        (
            price,
            anchor_source,
            anchor_region,
            pressures,
            formula_version,
            coefficients_version,
            anchor_p50,
            discount_pct,
        ) = await _resolve_and_recommend(conn, pricing_input)

        row = await conn.fetchrow(
            _INSERT_SQL,
            original["listing_id"],
            original["merchant_id"],
            original["partner_id"],
            pricing_input.model_dump_json(),
            price,
            discount_pct,
            anchor_p50,
            anchor_source,
            anchor_region,
            json.dumps(pressures),
            formula_version,
            coefficients_version,
            recommendation_id,
        )

    assert row is not None
    logger.info(
        "pricing.replay_logged",
        extra={
            "recommendation_id": str(row["recommendation_id"]),
            "replay_of": str(recommendation_id),
            "partner_id": original["partner_id"],
            "formula_version": formula_version,
            "coefficients_version": coefficients_version,
        },
    )
    return _row_to_entry(
        dict(row),
        listing_id=original["listing_id"],
        merchant_id=original["merchant_id"],
        partner_id=original["partner_id"],
        pricing_input=pricing_input,
        recommended_price=price,
        recommended_discount_pct=discount_pct,
        anchor_p50=anchor_p50,
        anchor_source=anchor_source,
        anchor_region=anchor_region,
        applied_pressures=pressures,
        formula_version=formula_version,
        coefficients_version=coefficients_version,
        replay_of=recommendation_id,
    )
