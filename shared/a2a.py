"""Agent-to-Agent (A2A) client for IAM-authenticated peer agents.

Each call mints a Google ID token whose audience matches the target service
URL (Cloud Run gateway, Agent Engine endpoint), then POSTs an `AgentRequest`
JSON to the peer's `/v1/agent` endpoint. Tokens are cached per audience for
~50 minutes to amortize the metadata-server round trip on hot paths.

Pattern ported verbatim from `SurplusAS-API-2.0/shared/a2a.py:45`.

Local dev: ID-token minting via ADC requires either an attached service
account (Cloud Run / GCE / Cloud Build) or
`gcloud auth application-default login --impersonate-service-account=...`.
A plain user ADC cannot mint audience-scoped ID tokens.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import google.auth.transport.requests
import httpx
from google.oauth2 import id_token

from shared.tracing import a2a_client_span

logger = logging.getLogger("surplusas.a2a")


_token_cache: dict[str, tuple[str, float]] = {}


def _fetch_id_token(audience: str) -> str:
    """Mint (or return cached) Google ID token whose audience matches `audience`."""
    cached = _token_cache.get(audience)
    now = time.time()
    if cached and cached[1] > now + 60:
        return cached[0]

    auth_req = google.auth.transport.requests.Request()
    token: str = id_token.fetch_id_token(auth_req, audience)  # type: ignore[no-untyped-call]
    # ID tokens last 1 hour; cache for 50 minutes.
    _token_cache[audience] = (token, now + 50 * 60)
    return token


async def call_peer_agent(
    audience: str,
    body: dict[str, Any],
    path: str = "/v1/agent",
    timeout: float = 120.0,  # noqa: ASYNC109 — passed to httpx, not a stand-in for asyncio.timeout
) -> dict[str, Any]:
    """POST `body` to a peer agent service and return the parsed JSON.

    Raises `httpx.HTTPStatusError` on non-2xx responses.
    """
    token = _fetch_id_token(audience)
    url = audience.rstrip("/") + path

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    with a2a_client_span(audience, headers):
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, json=body, headers=headers)
            r.raise_for_status()
            data: dict[str, Any] = r.json()
            return data
