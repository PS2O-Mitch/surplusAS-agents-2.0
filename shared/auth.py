"""Bearer-token auth for the public REST surface.

Resolves `Authorization: Bearer <api_key>` against `public.partner_keys` and
returns a `PartnerContext`. Cached in-process for 5 minutes per api_key. The
underlying table is owned by `SurplusAS-API-2.0`; this module only reads it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Header, HTTPException, status

from shared.db import fetch_one


@dataclass(slots=True)
class PartnerContext:
    api_key: str
    partner_id: str
    context: dict[str, Any]


_PARTNER_CACHE: dict[str, tuple[PartnerContext, float]] = {}
_TTL_SECONDS = 300.0


async def _resolve_api_key(api_key: str) -> PartnerContext:
    cached = _PARTNER_CACHE.get(api_key)
    now = time.time()
    if cached and cached[1] > now:
        return cached[0]

    row = await fetch_one(
        "SELECT api_key, partner_id, context_json FROM public.partner_keys WHERE api_key = $1",
        api_key,
    )
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid api_key")

    ctx = PartnerContext(
        api_key=row["api_key"],
        partner_id=row["partner_id"],
        context=row["context_json"] or {},
    )
    _PARTNER_CACHE[api_key] = (ctx, now + _TTL_SECONDS)
    return ctx


async def require_partner(
    authorization: str = Header(..., alias="Authorization"),
) -> PartnerContext:
    """FastAPI dependency: extract bearer token and resolve to a `PartnerContext`."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    # `authorization[len("Bearer "):]` handles the all-whitespace tail cleanly;
    # `.split(None, 1)[1]` would IndexError on "Bearer " because there's no
    # second token to split into.
    api_key = authorization[len("Bearer "):].strip()
    if not api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Empty api_key")
    return await _resolve_api_key(api_key)


PartnerDep = Depends(require_partner)
