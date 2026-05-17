"""CRUD helpers for `agents.webhook_subscriptions`.

The per-subscription `secret` is what the CUSTOMER will eventually use to
verify *inbound* requests from us (Phase 5 deferred). For *outbound*
signing (Phase 4), `shared.webhook_dispatcher` uses the repo-wide
`WEBHOOK_SIGNING_KEY`. We still store the secret hash here so the
subscription create endpoint accepts the customer's chosen secret and we
can verify-but-not-recover it later.

Hashing: SHA-256 (not argon2id) for now — it's fast and we never need to
recover the value. The schema column name `secret_hash` is preserved.
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import UUID

from shared.db import execute, fetch_all, fetch_one, init_pool

_MIN_SECRET_LENGTH = 16


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


async def create_subscription(
    *,
    partner_id: str,
    url: str,
    events: list[str],
    secret: str,
) -> dict[str, Any]:
    """INSERT a new active subscription and return its `subscription_id`.

    Validation:
      - `url` must use `https://` (no plaintext webhook endpoints in prod).
      - `events` non-empty (otherwise the subscription is silent — likely a bug).
      - `secret` >= 16 chars (entropy floor; HMAC keys benefit from length).
    """
    if not url.startswith("https://"):
        return {"status": "validation_error",
                "error": "url must use https://", "field": "url"}
    if not events:
        return {"status": "validation_error",
                "error": "events must be a non-empty list", "field": "events"}
    if not secret or len(secret) < _MIN_SECRET_LENGTH:
        return {"status": "validation_error",
                "error": f"secret must be at least {_MIN_SECRET_LENGTH} chars",
                "field": "secret"}

    await init_pool()
    row = await fetch_one(
        "INSERT INTO agents.webhook_subscriptions "
        "  (partner_id, url, events, secret_hash, active) "
        "VALUES ($1, $2, $3, $4, TRUE) "
        "RETURNING subscription_id",
        partner_id, url, list(events), _hash_secret(secret),
    )
    assert row is not None
    return {"status": "ok", "subscription_id": str(row["subscription_id"])}


async def list_active_subscriptions_for_event(
    *,
    partner_id: str,
    event_type: str,
) -> list[dict[str, Any]]:
    """Return active subscriptions whose `events` array contains `event_type`."""
    await init_pool()
    rows = await fetch_all(
        "SELECT subscription_id, url, events "
        "FROM agents.webhook_subscriptions "
        "WHERE partner_id = $1 AND active = TRUE AND $2 = ANY(events)",
        partner_id, event_type,
    )
    return [dict(r) for r in rows]


async def deactivate_subscription(
    *,
    subscription_id: str,
    partner_id: str,
) -> dict[str, Any]:
    """Soft-delete: set active=FALSE. Idempotent (does not error if already off).

    Scoped to the caller's `partner_id` so one tenant can't deactivate
    another's subscription via UUID guess.
    """
    try:
        sub_uuid = UUID(subscription_id)
    except ValueError:
        return {"status": "validation_error",
                "error": f"invalid UUID {subscription_id!r}",
                "field": "subscription_id"}

    await init_pool()
    result = await execute(
        "UPDATE agents.webhook_subscriptions "
        "SET active = FALSE "
        "WHERE subscription_id = $1 AND partner_id = $2",
        sub_uuid, partner_id,
    )
    return {"status": "ok", "rows": result}
