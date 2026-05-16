"""Minimal smoke test for the deployed Concierge agent.

Sends a natural-language message and streams events. The model is expected
to pick exactly one routing tool per turn (route_to_onboarding /
route_to_listing_intake / route_to_pricing / route_to_dispute_triage) and
narrate the specialist's response.

    uv run python -m scripts.smoke_concierge --ping
    uv run python -m scripts.smoke_concierge
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
                        help="trivial query that exercises no routing tool")
    args = parser.parse_args(argv)

    resource = os.environ.get("CONCIERGE_AGENT_RESOURCE")
    if not resource:
        print("ERROR: CONCIERGE_AGENT_RESOURCE not set", file=sys.stderr)
        return 1

    if args.ping:
        message = "What do you do? Answer in one sentence."
    else:
        # Onboarding-flavored turn — should route to onboarding.
        message = (
            "Hi, I'm Tampa Bagel Co — a deli in Hillsborough County, FL. "
            "We sell bagels and breakfast sandwiches. Use a 12% floor please."
        )

    asyncio.run(_run(resource, message, user_id="sk_demo_surplus_2026"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
