"""Same-origin demo shims for the bundled static UI.

The static page (`service/static/surplusas-merchant-demo.html`) can't carry an
API key — it's just HTML served from the same origin. The demo shim runs
unauthenticated and forces `partner_id=demo_001` — the partner the committed
demo key (`sk_demo_surplus_2026`) resolves to through `public.partner_keys`,
so shim-created rows are visible to the authenticated `/v1/*` surface. The
whole surface (shim + static mount) is only registered when `DEMO_MODE=true`
(`service/app.py`) — never in production.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from fastapi import APIRouter

from shared import a2a
from shared.db import fetch_one, init_pool

router = APIRouter(prefix="/demo/v1", tags=["demo"])
DEMO_PARTNER_ID = "demo_001"

_demo_merchant_cache: str | None = None


async def _demo_merchant_id() -> str | None:
    """The demo partner's first merchant profile, cached for the process.

    The static page never sends a merchant_id, but Listing Intake resolves
    region/floor/local-hour from the merchant profile — without one every
    demo listing dead-ends in an onboarding prompt. Errors resolve to None
    so the shim never 500s here; the agent just asks the merchant to
    onboard first.
    """
    global _demo_merchant_cache
    if _demo_merchant_cache is None:
        with suppress(Exception):
            await init_pool()
            row = await fetch_one(
                "SELECT merchant_id FROM agents.merchant_profiles "
                "WHERE partner_id = $1 ORDER BY created_at LIMIT 1",
                DEMO_PARTNER_ID,
            )
            if row is not None:
                _demo_merchant_cache = str(row["merchant_id"])
    return _demo_merchant_cache


# listings + the recommendation that priced them — same JOIN the authenticated
# GET /v1/listings/{id} uses (service/routes_rest.py); applied_pressures comes
# back as a dict via the JSONB codec in shared/db.py.
_LISTING_ENRICH_SQL = (
    "SELECT l.listing_id, l.title, l.description, l.category, l.units, "
    "       l.retail_value, l.hours_until_expiry, l.status, "
    "       r.recommendation_id, r.recommended_price, r.recommended_discount_pct, "
    "       r.anchor_p50, r.applied_pressures, r.formula_version "
    "FROM agents.listings l "
    "JOIN agents.recommendation_log r "
    "  ON r.recommendation_id = l.current_recommendation_id "
    "WHERE l.listing_id = $1 AND l.partner_id = $2"
)


def _extract_persisted(tool_calls: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Last successful persist_listing response in the stream, or None.

    Reverse scan: if the model retried persist_listing (validation error,
    transient DB failure), the LAST ok response is the row its final
    narration refers to.
    """
    for tc in reversed(tool_calls):
        if tc.get("name") != "persist_listing":
            continue
        resp = tc.get("response")
        if isinstance(resp, dict) and resp.get("status") == "ok" and resp.get("listing_id"):
            return resp
    return None


async def _demo_context_prefix() -> str:
    """Bracketed identity prefix the agent prompts require (verbatim-copy rule)."""
    parts = [f"partner_id={DEMO_PARTNER_ID}"]
    merchant_id = await _demo_merchant_id()
    if merchant_id:
        parts.append(f"merchant_id={merchant_id}")
    return f"[{', '.join(parts)}]"


async def _intake_turn(user_message: str) -> dict[str, Any]:
    """One Listing Intake turn -> the demo page's clean envelope.

    Calls the intake agent directly (not via the Concierge) so
    persist_listing's tool response is visible in tool_calls, then
    enriches from the DB — the UI never carries a price and this shim
    never writes agents.listings (persist_listing owns those writes).

    - ok:            {status, narration, listing: {...}, pricing: {...}}
    - clarification: {status, narration} — the agent asked a question or
                     validation/no_anchor parked the draft; nothing persisted.
    - error:         {status, narration, error, listing_id} — persisted but
                     the read-back JOIN failed; degrade loudly, never fake a card.
    """
    agg = await a2a.aggregate_peer_stream(
        "listing_intake", user_message, DEMO_PARTNER_ID,
    )
    persisted = _extract_persisted(agg["tool_calls"])
    if persisted is None:
        return {"status": "clarification", "narration": agg["narration"]}

    row: dict[str, Any] | None = None
    with suppress(Exception):
        await init_pool()
        row = await fetch_one(
            _LISTING_ENRICH_SQL, persisted["listing_id"], DEMO_PARTNER_ID,
        )
    if row is None:
        return {
            "status": "error",
            "narration": agg["narration"],
            "error": "listing persisted but could not be read back",
            "listing_id": persisted["listing_id"],
        }

    return {
        "status": "ok",
        "narration": agg["narration"],
        "listing": {
            "listing_id": row["listing_id"],
            "title": row["title"],
            "description": row["description"],
            "category": row["category"],
            "units": row["units"],
            "retail_value": row["retail_value"],
            "hours_until_expiry": row["hours_until_expiry"],
            "status": row["status"],
        },
        "pricing": {
            "recommendation_id": row["recommendation_id"],
            "recommended_price": row["recommended_price"],
            "recommended_discount_pct": row["recommended_discount_pct"],
            "anchor_p50": row["anchor_p50"],
            "applied_pressures": row["applied_pressures"],
            "formula_version": row["formula_version"],
        },
    }


@router.post("/agent")
async def demo_agent(body: dict[str, Any]) -> dict[str, Any]:
    """Delegate to /v1/concierge with a fixed demo partner_id."""
    if not body.get("merchant_id"):
        body["merchant_id"] = await _demo_merchant_id()
    user_message = _compose_concierge_message(body)
    aggregated = await a2a.call_concierge(
        user_message=user_message,
        partner_id=DEMO_PARTNER_ID,
    )
    return {
        "narration": aggregated["narration"],
        "specialist_called": aggregated["specialist_called"],
        "specialist_payload": aggregated["specialist_payload"],
    }


def _compose_concierge_message(body: dict[str, Any]) -> str:
    """Extract the merchant's text from either the new or legacy envelope.

    The current REST contract uses `{message, merchant_id?, listing_id?, image?}`.
    The bundled static demo (`service/static/surplusas-merchant-demo.html`) is
    a port from SurplusAS-API-2.0 and still sends the legacy
    `{mode, input, image?}` envelope — see line 863 of the page. Accept both
    shapes here so the page works without a rewrite.

    ADK's Runner accepts a string (auto-wrapped as user content) but not a
    dict envelope, so we materialise the actual merchant text into a string.
    Context fields surface as a leading bracketed prefix.
    """
    message = body.get("message") or body.get("input") or ""
    # partner_id always injected — see routes_rest._compose_concierge_message.
    parts: list[str] = [f"partner_id={DEMO_PARTNER_ID}"]
    if body.get("merchant_id"):
        parts.append(f"merchant_id={body['merchant_id']}")
    if body.get("listing_id"):
        parts.append(f"listing_id={body['listing_id']}")
    return f"[{', '.join(parts)}] {message}"


@router.post("/listings/generate")
async def demo_generate_listing(body: dict[str, Any]) -> dict[str, Any]:
    """Static UI Beat 1: merchant note -> Listing Intake -> reviewable draft.

    The persisted row has status='draft'; the page renders it editable and
    a later /listings/publish run creates the published row.
    """
    note = str(body.get("note") or "").strip()
    if not note:
        return {"error": "note is required"}
    prefix = await _demo_context_prefix()
    return await _intake_turn(f"{prefix} {note}")


_REQUIRED_PUBLISH_FIELDS = ("title", "category", "units", "retail_value",
                            "hours_until_expiry")


@router.post("/listings/publish")
async def demo_publish_listing(body: dict[str, Any]) -> dict[str, Any]:
    """Static UI Beat 1b: publish the reviewed (possibly edited) fields.

    Runs the full intake flow again on the edited fields — validate, fresh
    deterministic re-price, persist with status='published'. Intentionally
    creates a SECOND listings row: the draft row from /generate stays as
    audit trail, and edited retail/expiry get a fresh engine-priced
    recommendation (a price is never carried from the UI). Edited fields
    are interpolated into an LLM message; DEMO_MODE-gated surface with the
    partner pinned server-side, so worst case is a weird row under demo_001.
    """
    fields = body.get("listing")
    if not isinstance(fields, dict):
        return {"error": "listing object is required"}
    missing = [f for f in _REQUIRED_PUBLISH_FIELDS
               if not str(fields.get(f) or "").strip()]
    if missing:
        return {"error": f"listing is missing required fields: {', '.join(missing)}"}

    prefix = await _demo_context_prefix()
    user_message = (
        f"{prefix} Publish this reviewed listing with status='published'. "
        "Use these exact field values as the draft:\n"
        f"title: {fields.get('title')}\n"
        f"description: {fields.get('description') or ''}\n"
        f"category: {fields.get('category')}\n"
        f"units: {fields.get('units')}\n"
        f"retail_value: {fields.get('retail_value')}\n"
        f"hours_until_expiry: {fields.get('hours_until_expiry')}"
    )
    return await _intake_turn(user_message)


@router.post("/listings/{listing_id}/dispute")
async def demo_open_dispute(
    listing_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Demo shim: open a dispute on an existing listing.

    Mirrors `/v1/listings/{listing_id}/dispute` but forces the demo
    partner so the bundled static UI can drive Beat 2 without an API
    key. Routes through Dispute Triage via aggregate_peer_stream so we
    return the human-readable narration the demo UI renders.
    """
    reason = (body.get("reason") or "").strip()
    if not reason:
        return {"error": "reason is required"}
    user_message = (
        f"Dispute on listing_id={listing_id}: {reason}"
    )
    agg = await a2a.aggregate_peer_stream(
        "dispute_triage", user_message, DEMO_PARTNER_ID,
    )
    return {
        "listing_id": listing_id,
        "narration": agg["narration"],
        "tool_calls": agg["tool_calls"],
        "event_count": agg["event_count"],
    }
