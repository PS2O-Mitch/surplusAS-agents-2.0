# shared/webhook_dispatcher.py
"""HMAC-SHA256-signed webhook delivery.

Phase 4 is sync-with-audit-row: caller INSERTs an `agents.webhook_deliveries`
row, then awaits `deliver(...)`, then UPDATEs the row with the outcome.
No background retry worker yet (Phase 5/6). Failed rows accumulate with
`delivered_at IS NULL` so a worker can sweep them later.

Signing: header `X-Surplus-Signature: sha256=<hex>` where <hex> is the
HMAC-SHA256 of the **JSON-encoded request body** (separators=`(",", ":")`)
using the repo-wide `WEBHOOK_SIGNING_KEY` from Secret Manager. Per-
subscription secrets in `agents.webhook_subscriptions.secret_hash` are
reserved for future *inbound* verification (Phase 5).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx

_DEFAULT_TIMEOUT_S = 10.0


def compute_signature(body: bytes, signing_key: str) -> str:
    """Return the `X-Surplus-Signature` header value for `body`."""
    digest = hmac.new(
        signing_key.encode("utf-8"), body, hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


async def deliver(
    *,
    url: str,
    payload: dict[str, Any],
    signing_key: str,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """POST `payload` (JSON) to `url` with `X-Surplus-Signature` header.

    Returns `{status_code, error?}`. 2xx is success; anything else is an
    error string the caller persists. Network errors come back as
    `{status_code: 0, error: <repr>}`.
    """
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "X-Surplus-Signature": compute_signature(body, signing_key),
        "User-Agent": "surplusas-agents/0.1",
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url, content=body, headers=headers, timeout=timeout_s,
            )
        if 200 <= resp.status_code < 300:
            return {"status_code": resp.status_code}
        return {"status_code": resp.status_code, "error": resp.text[:500]}
    except Exception as exc:  # noqa: BLE001 — single boundary, surfaced verbatim
        return {"status_code": 0, "error": repr(exc)[:500]}
