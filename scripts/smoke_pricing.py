"""Minimal smoke test for the deployed Pricing agent.

Calls `async_stream_query` against the resource in `PRICING_AGENT_RESOURCE`
and streams events. First mode (`--ping`) sends a trivial question that the
agent can answer without tool invocation; the second mode (default) sends a
real `price_listing` envelope so the engine_adapter / DB / pricing engine
chain is exercised end-to-end.

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
        message = {
            "mode": "price_listing",
            "input": {
                "category": "prepared_meal",
                "region": "US-FL-Hillsborough",
                "units": 1,
                "retail_value": 12.00,
                "hours_until_expiry": 4.0,
                "now_hour": 18,
                "merchant_floor_pct": 0.10,
            },
        }

    asyncio.run(_run(resource, message, user_id="sk_demo_surplus_2026"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
