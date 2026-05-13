"""Minimal smoke test for the deployed Pricing agent.

Calls `async_stream_query` against the resource in `PRICING_AGENT_RESOURCE`
and streams events. First mode (`--ping`) sends a trivial question the
agent answers without tool use; the second mode (default) sends a
natural-language prompt that nudges the LLM to invoke `price_listing` with
specific parameters — which exercises the engine_adapter / DB / pricing
engine chain end-to-end.

Note: ADK's Runner accepts a string (auto-wrapped to user content) or a
`{role, parts: [...]}` dict. Sending custom-shape envelopes like
`{mode, input}` produces a 498 validation error on `Content` — there is no
direct-route-to-tool API exposed via `async_stream_query`; the LLM is
always in the loop. So tool exercise here goes through the model.

Prints every streamed event so we can see what the deployed agent produces
before the gateway reads it.

    uv run python -m scripts.smoke_pricing --ping
    uv run python -m scripts.smoke_pricing
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
    parser.add_argument("--ping", action="store_true", help="send a trivial natural-language query")
    args = parser.parse_args(argv)

    resource = os.environ.get("PRICING_AGENT_RESOURCE")
    if not resource:
        print("ERROR: PRICING_AGENT_RESOURCE not set", file=sys.stderr)
        return 1

    if args.ping:
        message = "Say hello and tell me your role in one sentence."
    else:
        # Natural-language prompt the LLM should translate into a single
        # price_listing tool call. Every required arg is named in the
        # text so the model has no excuse to omit one. partner_id is the
        # demo key we issue in the seed data; if you change seed values,
        # update this string too.
        message = (
            "Please price this listing using the price_listing tool. "
            "Category: prepared_meal. Region: US-FL-Hillsborough. "
            "Units: 1. Retail value: $12.00. Hours until expiry: 4.0. "
            "Current hour (24h): 18. Merchant floor pct: 0.10. "
            "Partner id: sk_demo_surplus_2026."
        )

    asyncio.run(_run(resource, message, user_id="sk_demo_surplus_2026"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
