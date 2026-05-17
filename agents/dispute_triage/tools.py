"""ADK tools for the Dispute Triage agent.

Five tools (E1-E5 of the Phase 4 plan). Flow per master plan §4.5:
    fetch_recommendation_log(listing_id)  -> RecommendationLogEntry
    request_reprice(original_recommendation_id, partner_id)
                                          -> structured replay payload (lateral A2A -> Pricing)
    diff_pressures(old, new)              -> dict[str, float]
    persist_dispute(...)                  -> {"dispute_id": ...}
    emit_price_update_webhook(...)        -> delivery audit

This task (E1) adds only `fetch_recommendation_log`.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from shared import a2a
from shared.db import fetch_one, init_pool


async def fetch_recommendation_log(*, listing_id: str) -> dict[str, Any]:
    """Return the most recent recommendation_log row for `listing_id`.

    Going through `recommendation_log` directly (ORDER BY created_at DESC
    LIMIT 1) avoids assuming `agents.listings.current_recommendation_id` is
    in sync. The Dispute Triage prompt treats this row's `recommended_price`
    + `applied_pressures` as the "old" side of the diff.
    """
    try:
        UUID(listing_id)
    except ValueError:
        return {"status": "validation_error",
                "error": f"listing_id {listing_id!r} is not a valid UUID",
                "field": "listing_id"}

    await init_pool()
    row = await fetch_one(
        "SELECT recommendation_id, listing_id, merchant_id, partner_id, "
        "       pricing_input, recommended_price, recommended_discount_pct, "
        "       anchor_p50, anchor_source, anchor_region, "
        "       applied_pressures, formula_version, coefficients_version, "
        "       replay_of "
        "FROM agents.recommendation_log "
        "WHERE listing_id = $1 "
        "ORDER BY created_at DESC LIMIT 1",
        listing_id,
    )
    if row is None:
        return {"status": "not_found",
                "error": f"no recommendation found for listing_id {listing_id!r}"}
    return {"status": "ok", "recommendation": dict(row)}


async def diff_pressures(*, old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Compute per-pressure delta (`new - old`).

    Numeric pressures: float delta. Boolean clamp flags: -1/0/+1 transition.
    Keys present in only one map carry the present value as the delta
    (with the missing side treated as 0 / False).

    Pure function — no DB, no A2A. Safe to call inside the model's
    reasoning loop without IO.

    async for ADK tool-surface consistency (see Phase 3 A2 review feedback).
    """
    keys = set(old) | set(new)
    deltas: dict[str, Any] = {}
    for k in keys:
        old_v = old.get(k, 0)
        new_v = new.get(k, 0)
        if isinstance(old_v, bool) or isinstance(new_v, bool):
            deltas[k] = int(bool(new_v)) - int(bool(old_v))
        else:
            deltas[k] = float(new_v) - float(old_v)
    return {"status": "ok", "deltas": deltas}


async def request_reprice(
    *,
    original_recommendation_id: str,
    partner_id: str,
) -> dict[str, Any]:
    """Ask Pricing to replay a prior recommendation under fresh coefficients.

    Lateral A2A edge per CLAUDE.md ("Dispute Triage -> Pricing"). The Pricing
    agent's `replay_recommendation` tool will INSERT a new `recommendation_log`
    row tagged `replay_of=<original_recommendation_id>` and return its summary
    as the `function_response` of the `replay_recommendation` tool call. We
    extract that response from the aggregated tool_calls so the model gets a
    structured `{new_recommendation_id, new_price, new_pressures}` payload —
    no narration parsing required downstream.
    """
    try:
        UUID(original_recommendation_id)
    except ValueError:
        return {"status": "validation_error",
                "error": f"recommendation_id {original_recommendation_id!r} is not a valid UUID",
                "field": "recommendation_id"}

    user_message = (
        "Please replay this prior recommendation using the replay_recommendation tool. "
        f"Recommendation id: {original_recommendation_id}. "
        f"Partner id: {partner_id}."
    )
    agg = await a2a.aggregate_peer_stream("pricing", user_message, partner_id)

    replay_resp: dict[str, Any] | None = None
    for tc in agg.get("tool_calls", []):
        if tc.get("name") == "replay_recommendation" and isinstance(
            tc.get("response"), dict,
        ):
            replay_resp = tc["response"]
            break

    if replay_resp is None or "recommendation" not in replay_resp:
        return {"status": "error",
                "error": "Pricing did not return a replay_recommendation "
                         "tool response — cannot derive new_recommendation_id",
                "narration": agg.get("narration", "")}

    rec = replay_resp["recommendation"]
    return {
        "status": "ok",
        "new_recommendation_id": str(rec["recommendation_id"]),
        "new_price": float(rec["recommended_price"]),
        "new_pressures": dict(rec.get("applied_pressures", {})),
        "narration": agg["narration"],
    }


async def persist_dispute(
    *,
    listing_id: str,
    merchant_id: str,
    partner_id: str,
    reason_text: str,
    original_recommendation_id: str,
    new_recommendation_id: str,
    pressure_diff: dict[str, Any],
) -> dict[str, Any]:
    """Insert a row into `agents.disputes`.

    Schema is from `shared/db_schema.sql:88`. Resolution defaults to 'pending';
    the lifecycle endpoints that move it to 'accepted'/'rejected'/'withdrawn'
    are Phase 5 work.

    `pressure_diff` is the output of `diff_pressures(...)` — a flat dict of
    per-key numeric deltas + bool transitions. Serialised to JSONB at bind
    time so asyncpg passes it as a single typed parameter (no JSON column
    surgery in the model).
    """
    try:
        l_uuid = UUID(listing_id)
        m_uuid = UUID(merchant_id)
        orig_uuid = UUID(original_recommendation_id)
        new_uuid = UUID(new_recommendation_id)
    except ValueError as exc:
        return {"status": "validation_error",
                "error": f"invalid UUID: {exc}", "field": "uuid"}

    if not reason_text or not reason_text.strip():
        return {"status": "validation_error",
                "error": "reason_text must be non-empty", "field": "reason_text"}

    await init_pool()
    row = await fetch_one(
        "INSERT INTO agents.disputes "
        "  (listing_id, merchant_id, partner_id, reason_text, "
        "   original_recommendation_id, new_recommendation_id, pressure_diff) "
        "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb) "
        "RETURNING dispute_id",
        l_uuid, m_uuid, partner_id, reason_text,
        orig_uuid, new_uuid, json.dumps(pressure_diff),
    )
    assert row is not None
    return {"status": "ok", "dispute_id": str(row["dispute_id"])}
