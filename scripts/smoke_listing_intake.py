"""Minimal smoke test for the deployed Listing Intake agent.

Sends a natural-language draft over `async_stream_query` and streams events.
The model is expected to invoke `parse_draft` -> `validate_listing` ->
`request_anchor_price` -> `persist_listing` in sequence, ending with a
narration that references the saved listing_id and the dominant pressure.

Like `smoke_pricing.py`, this sends a string message (not a custom envelope)
because ADK's Runner only accepts text or `{role, parts:[...]}` shapes; the
deployed agent's prompt does the tool dispatch.

    uv run python -m scripts.smoke_listing_intake --ping
    uv run python -m scripts.smoke_listing_intake
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any


async def _run(resource: str, message: Any, user_id: str) -> None:
    import vertexai
    from vertexai import agent_engines

    project = os.environ.get("GOOGLE_CLOUD_PROJECT", "ps2o-surplusas-api")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    vertexai.init(project=project, location=location)

    handle = agent_engines.get(resource)
    print(f"resolved handle: {handle.resource_name}")
    print(f"sending message: {json.dumps(message, default=str)[:300]}")
    print("---")

    n = 0
    async for event in handle.async_stream_query(message=message, user_id=user_id):
        n += 1
        print(f"[event {n}] {json.dumps(event, default=str)[:1000]}")
    print(f"---\ntotal events: {n}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ping", action="store_true",
                        help="trivial query that exercises no tools")
    args = parser.parse_args(argv)

    resource = os.environ.get("LISTING_INTAKE_AGENT_RESOURCE")
    if not resource:
        print("ERROR: LISTING_INTAKE_AGENT_RESOURCE not set", file=sys.stderr)
        return 1

    if args.ping:
        message = "Tell me what you do in one sentence."
    else:
        # Draft text + context the model needs to fill the 4-tool chain.
        # partner_id, region, merchant_floor_pct, now_hour, and a known
        # merchant_id are spelled out so the model has every kwarg it needs.
        message = (
            "Save this draft. Title: Day-old turkey sandwiches. "
            "Description: deli-made, refrigerated. Category: prepared_meal. "
            "Units: 10. Retail value: $12.00. Hours until expiry: 4. "
            "Image URI: none. "
            "Region: US-FL-Hillsborough. Merchant floor pct: 0.10. "
            "Now hour (24h): 18. Partner id: sk_demo_surplus_2026. "
            "Merchant id: 00000000-0000-0000-0000-000000000001."
        )

    asyncio.run(_run(resource, message, user_id="sk_demo_surplus_2026"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
