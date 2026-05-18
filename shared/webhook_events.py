# shared/webhook_events.py
"""Outbound webhook orchestrator.

`emit_event(event_type, partner_id, payload)` is the single entry point
for agents (Onboarding, Listing Intake, Dispute Triage) to fire webhooks.
It looks up active subscriptions for `partner_id`+`event_type`, writes a
`webhook_deliveries` row per subscription, attempts delivery, and updates
the row with the outcome.

No background retry yet (Phase 5/6). Failed rows have `delivered_at IS NULL`
and a populated `last_status_code` / `last_error` for later replay.

Contract with `deliver`: `deliver` is designed to never raise — all network
exceptions surface as `{status_code: 0, error: ...}`. This means the per-sub
iteration loop is safe without a try/except wrapper. If that contract ever
develops a hole, only the remaining subs in the loop would be skipped; the
audit row for the failing sub is already INSERTed, so no delivery is lost
from an audit perspective.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from shared.config import get_settings
from shared.db import execute, fetch_one, init_pool
from shared.webhook_dispatcher import deliver
from shared.webhook_subscriptions import list_active_subscriptions_for_event


async def emit_event(
    *,
    event_type: str,
    partner_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Fan-out delivery to all active subscriptions matching event_type.

    Wraps the full event in {event_id, event_type, partner_id, occurred_at,
    payload}, writes a `webhook_deliveries` audit row per subscription,
    POSTs with HMAC signature, then UPDATEs the row with the outcome.

    Returns `{status: "ok", delivery_ids: [...]}` regardless of individual
    delivery outcomes — failures are recorded in the rows for later replay.
    """
    subs = await list_active_subscriptions_for_event(
        partner_id=partner_id, event_type=event_type,
    )
    if not subs:
        return {"status": "ok", "delivery_ids": []}

    signing_key = get_settings().webhook_signing_key
    occurred_at = datetime.now(UTC).isoformat()
    event_id = str(uuid4())

    full_payload = {
        "event_id": event_id,
        "event_type": event_type,
        "partner_id": partner_id,
        "occurred_at": occurred_at,
        "payload": payload,
    }

    delivery_ids: list[str] = []
    await init_pool()

    for sub in subs:
        row = await fetch_one(
            "INSERT INTO agents.webhook_deliveries "
            "  (subscription_id, event_type, payload, attempt, last_attempt_at) "
            "VALUES ($1, $2, $3::jsonb, 1, NOW()) "
            "RETURNING delivery_id",
            UUID(sub["subscription_id"]), event_type,
            json.dumps(full_payload),
        )
        assert row is not None
        delivery_id = str(row["delivery_id"])
        delivery_ids.append(delivery_id)

        outcome = await deliver(
            url=sub["url"], payload=full_payload, signing_key=signing_key,
        )
        if 200 <= outcome["status_code"] < 300:
            await execute(
                "UPDATE agents.webhook_deliveries "
                "SET last_status_code = $2, delivered_at = NOW() "
                "WHERE delivery_id = $1",
                UUID(delivery_id), outcome["status_code"],
            )
        else:
            await execute(
                "UPDATE agents.webhook_deliveries "
                "SET last_status_code = $2, last_error = $3 "
                "WHERE delivery_id = $1",
                UUID(delivery_id), outcome["status_code"],
                outcome.get("error", "")[:1000],
            )

    return {"status": "ok", "delivery_ids": delivery_ids}
