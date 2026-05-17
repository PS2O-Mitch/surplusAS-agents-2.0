"""Minimal smoke test for the deployed Dispute Triage agent.

Sends a freeform dispute message and streams events. The model is expected
to invoke `fetch_recommendation_log` → `request_reprice` →
`diff_pressures` → `persist_dispute` → `emit_price_update_webhook` in
sequence and end with a merchant-facing narration that names the dominant
pressure mover.

For the --ping mode we just probe self-identification (no DB seeding).
A full flow smoke requires a real listing in `agents.listings` and a
real recommendation_log row — deferred to manual Beat 2 dry-run.

    uv run python -m scripts.smoke_dispute_triage --ping
    uv run python -m scripts.smoke_dispute_triage
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

    resource = os.environ.get("DISPUTE_TRIAGE_AGENT_RESOURCE")
    if not resource:
        print("ERROR: DISPUTE_TRIAGE_AGENT_RESOURCE not set", file=sys.stderr)
        return 1

    if args.ping:
        message = "Tell me what you do in one sentence."
    else:
        # Freeform dispute. Requires a real listing row to fully exercise
        # the chain; with no row, fetch_recommendation_log returns
        # not_found and the agent should surface that cleanly.
        message = (
            "Dispute on listing_id=22222222-2222-2222-2222-222222222222: "
            "price dropped too fast. Partner id: sk_demo_surplus_2026."
        )

    asyncio.run(_run(resource, message, user_id="sk_demo_surplus_2026"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
