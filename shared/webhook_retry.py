# shared/webhook_retry.py
"""Async webhook retry sweep.

`retry_failed_deliveries(limit)` is the workhorse: scan
`agents.webhook_deliveries` for failed-and-eligible-for-retry rows
(joined with their active subscriptions for the current URL), redeliver,
UPDATE the row with the outcome. Exponential backoff (2^attempt seconds),
cap at 5 attempts.

Called by the gateway's background loop (see `service/app.py:_lifespan`,
landed in Task O3). Designed as a single-shot sweep so it's trivially
unit-testable: pass a `limit`, get back a summary dict.

Eligibility criteria (encoded in the SELECT):
  - delivered_at IS NULL  (never succeeded)
  - attempt < 5           (not dead-lettered yet)
  - subscription.active   (subscription still wants webhooks)
  - COALESCE(last_attempt_at, created_at) + (POWER(2, attempt) seconds) <= NOW()
                          (enough time has passed since last try)
"""

from __future__ import annotations

import json
from uuid import UUID

from shared.config import get_settings
from shared.db import execute, fetch_all, init_pool
from shared.webhook_dispatcher import deliver

_MAX_ATTEMPTS = 5


async def retry_failed_deliveries(*, limit: int = 100) -> dict[str, int]:
    """One sweep: find eligible candidates, redeliver, update rows.

    Returns `{scanned, retried, succeeded, failed, dead_lettered}` for
    observability. `dead_lettered` counts rows that reached attempt=5
    in this sweep (won't be retried again).
    """
    await init_pool()
    candidates = await fetch_all(
        "SELECT d.delivery_id, d.subscription_id, d.event_type, d.payload, "
        "       d.attempt, s.url "
        "FROM agents.webhook_deliveries d "
        "JOIN agents.webhook_subscriptions s ON s.subscription_id = d.subscription_id "
        "WHERE d.delivered_at IS NULL "
        "  AND d.attempt < $1 "
        "  AND s.active = TRUE "
        "  AND COALESCE(d.last_attempt_at, d.created_at) "
        "      + (POWER(2, d.attempt) * INTERVAL '1 second') <= NOW() "
        "ORDER BY d.created_at "
        "LIMIT $2",
        _MAX_ATTEMPTS, limit,
    )

    summary = {"scanned": len(candidates), "retried": 0,
               "succeeded": 0, "failed": 0, "dead_lettered": 0}
    if not candidates:
        return summary

    signing_key = get_settings().webhook_signing_key

    for row in candidates:
        # asyncpg returns JSONB as a dict when a codec is registered, else
        # as a JSON string. Handle both shapes defensively.
        raw_payload = row["payload"]
        payload = (
            json.loads(raw_payload) if isinstance(raw_payload, str)
            else raw_payload
        )

        outcome = await deliver(
            url=row["url"], payload=payload, signing_key=signing_key,
        )
        summary["retried"] += 1
        delivery_uuid = UUID(str(row["delivery_id"]))
        new_attempt = int(row["attempt"]) + 1

        if 200 <= outcome["status_code"] < 300:
            await execute(
                "UPDATE agents.webhook_deliveries "
                "SET attempt = attempt + 1, "
                "    last_status_code = $2, "
                "    last_attempt_at = NOW(), "
                "    delivered_at = NOW() "
                "WHERE delivery_id = $1",
                delivery_uuid, outcome["status_code"],
            )
            summary["succeeded"] += 1
        else:
            await execute(
                "UPDATE agents.webhook_deliveries "
                "SET attempt = attempt + 1, "
                "    last_status_code = $2, "
                "    last_error = $3, "
                "    last_attempt_at = NOW() "
                "WHERE delivery_id = $1",
                delivery_uuid, outcome["status_code"],
                outcome.get("error", "")[:1000],
            )
            summary["failed"] += 1
            if new_attempt >= _MAX_ATTEMPTS:
                summary["dead_lettered"] += 1

    return summary
