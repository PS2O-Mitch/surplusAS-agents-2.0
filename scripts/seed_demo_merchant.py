"""Reset the agents tables for a clean Beat 1 + Beat 2 demo run.

Wipes the agent-owned tables so the video shoot starts from a known
empty state. `public.partner_keys` is left alone (the demo partner
must already exist there — seeded once during the Phase 5 cutover).

What's wiped:
  agents.webhook_deliveries
  agents.webhook_subscriptions
  agents.disputes
  agents.listings
  agents.recommendation_log

What's NOT wiped:
  public.partner_keys             — credentials live here
  public.pricing_coefficients     — owned by surplusAS-pricing-intel
  agents.reference_prices         — refreshed by the nightly job

Prereqs (same as scripts/apply_schema.py):
  gcloud auth application-default login
  PG_USER, PG_PASSWORD exported in the environment

Usage:
  PG_USER=surplusas_app PG_PASSWORD='...' \
    uv run python scripts/seed_demo_merchant.py

The script also confirms the demo partner key
(`sk_demo_surplus_2026`) exists in `public.partner_keys`; if missing
it prints a SQL hint rather than auto-inserting (credentials are too
sensitive for an auto-seed to handle).
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import TYPE_CHECKING

from google.cloud.sql.connector import Connector, IPTypes

if TYPE_CHECKING:
    import asyncpg

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "ps2o-surplusas-api")
INSTANCE = os.environ.get(
    "CLOUD_SQL_INSTANCE",
    "ps2o-surplusas-api:us-central1:surplusas-db",
)
DB_NAME = os.environ.get("DB_NAME", "surplusas")
PG_USER = os.environ.get("PG_USER")
PG_PASSWORD = os.environ.get("PG_PASSWORD")
DEMO_PARTNER = "sk_demo_surplus_2026"

# Order matters — webhook_deliveries depends on webhook_subscriptions;
# disputes depend on recommendation_log; listings depend on
# recommendation_log. TRUNCATE ... CASCADE handles the FK closure, but
# we TRUNCATE explicitly to log what was wiped.
TRUNCATE_ORDER = (
    "agents.webhook_deliveries",
    "agents.webhook_subscriptions",
    "agents.disputes",
    "agents.listings",
    "agents.recommendation_log",
)


async def _seed() -> None:
    if not PG_USER or not PG_PASSWORD:
        sys.stderr.write("ERROR: PG_USER and PG_PASSWORD must be exported.\n")
        sys.exit(1)

    connector = Connector(loop=asyncio.get_running_loop())
    try:
        conn: asyncpg.Connection = await connector.connect_async(
            INSTANCE,
            "asyncpg",
            user=PG_USER,
            password=PG_PASSWORD,
            db=DB_NAME,
            ip_type=IPTypes.PUBLIC,
        )
        try:
            print(f"Connected to {INSTANCE}/{DB_NAME} as {PG_USER}")
            print()
            print("--- Pre-seed row counts ---")
            for tbl in TRUNCATE_ORDER:
                count = await conn.fetchval(f"SELECT COUNT(*) FROM {tbl}")
                print(f"  {tbl}: {count}")

            print()
            print("--- Wiping demo state ---")
            # Single TRUNCATE with CASCADE handles everything safely.
            await conn.execute(
                "TRUNCATE "
                + ", ".join(TRUNCATE_ORDER)
                + " RESTART IDENTITY CASCADE"
            )
            print("  TRUNCATE ... RESTART IDENTITY CASCADE: ok")

            print()
            print("--- Post-seed row counts ---")
            for tbl in TRUNCATE_ORDER:
                count = await conn.fetchval(f"SELECT COUNT(*) FROM {tbl}")
                print(f"  {tbl}: {count}")

            print()
            print("--- Partner key check ---")
            row = await conn.fetchrow(
                "SELECT api_key, partner_id, active "
                "FROM public.partner_keys WHERE api_key = $1",
                DEMO_PARTNER,
            )
            if row is None:
                print(f"  MISSING: {DEMO_PARTNER} not found in public.partner_keys")
                print(
                    "  Insert with (as schema owner):\n"
                    f"    INSERT INTO public.partner_keys (api_key, partner_id, active) "
                    f"VALUES ('{DEMO_PARTNER}', 'demo_001', TRUE);"
                )
            else:
                print(
                    f"  ok: api_key={row['api_key']} "
                    f"partner_id={row['partner_id']} active={row['active']}"
                )

            print()
            print("Demo reset complete.")
        finally:
            await conn.close()
    finally:
        await connector.close_async()


if __name__ == "__main__":
    asyncio.run(_seed())
