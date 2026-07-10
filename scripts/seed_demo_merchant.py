"""Reset the agents tables and seed the reference data for a working demo.

Wipes the agent-owned tables so a demo run starts from a known empty
state, then idempotently seeds the reference data a fresh Postgres needs:

  public.pricing_coefficients — 9 categories × region US, version 1
                                (via vendor jobs/seed_coefficients._insert_seeds)
  public.reference_prices    — one anchor row per category at region US,
                                sources honoring pricing_engine.anchors
                                SOURCE_PREFERENCE (apify for prepared
                                categories, off for grocery)

Partner keys are credentials, so they are only inserted when YOU supply
one: set DEMO_API_KEY to the key you want seeded (partner_id demo_001).
The repo-committed literal `sk_demo_surplus_2026` is for LOCAL demo use —
never seed it into an internet-reachable deployment.

What's wiped:
  agents.webhook_deliveries, agents.webhook_subscriptions,
  agents.disputes, agents.listings, agents.recommendation_log

Run with the OWNER dsn (Supabase `postgres` role):
  DATABASE_URL='postgresql://postgres:...' [DEMO_API_KEY=...] \
      uv run python scripts/seed_demo_merchant.py
"""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

import shared.pricing_intel  # noqa: F401 — puts vendor/surplusas-pricing on sys.path

DEMO_PARTNER_ID = "demo_001"

TRUNCATE_ORDER = (
    "agents.webhook_deliveries",
    "agents.webhook_subscriptions",
    "agents.disputes",
    "agents.listings",
    "agents.recommendation_log",
)

# One plausible anchor row per category at country level. Source + tier must
# satisfy pricing_engine.anchors.SOURCE_PREFERENCE or lookups return no_anchor.
# (category, tier, source, p25, p50, p75)
ANCHOR_SEEDS: tuple[tuple[str, str | None, str, float, float, float], ...] = (
    ("prepared_meal", "mid", "apify", 8.50, 11.50, 15.00),
    ("bakery",        "mid", "apify", 3.50, 5.00, 7.50),
    ("beverage",      "mid", "apify", 3.00, 4.50, 6.00),
    ("deli",          "mid", "apify", 7.00, 9.50, 12.50),
    ("produce",       None,  "off",   2.00, 3.50, 5.50),
    ("dairy",         None,  "off",   2.50, 4.00, 6.00),
    ("packaged_goods", None, "off",   3.00, 5.00, 8.00),
    ("frozen",        None,  "off",   4.00, 6.50, 9.00),
    ("mixed_bag",     None,  "off",   5.00, 8.00, 12.00),
)


async def _seed() -> None:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        sys.stderr.write("ERROR: DATABASE_URL must be exported.\n")
        sys.exit(1)

    conn = await asyncpg.connect(dsn)
    try:
        print("--- Wiping demo state ---")
        await conn.execute(
            "TRUNCATE " + ", ".join(TRUNCATE_ORDER) + " RESTART IDENTITY CASCADE"
        )
        print("  TRUNCATE ... RESTART IDENTITY CASCADE: ok")

        print()
        print("--- Partner key ---")
        demo_key = os.environ.get("DEMO_API_KEY", "")
        if demo_key:
            await conn.execute(
                "INSERT INTO public.partner_keys (api_key, partner_id, context_json, active) "
                "VALUES ($1, $2, '{}'::jsonb, TRUE) ON CONFLICT DO NOTHING",
                demo_key, DEMO_PARTNER_ID,
            )
            print(f"  ok: seeded partner_id={DEMO_PARTNER_ID} (key from DEMO_API_KEY)")
        else:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM public.partner_keys WHERE partner_id = $1",
                DEMO_PARTNER_ID,
            )
            print(f"  skipped (DEMO_API_KEY not set); {count} existing {DEMO_PARTNER_ID} key(s)")
            if not count:
                print("  Insert one with: DEMO_API_KEY=<key> ... or SQL:")
                print("    INSERT INTO public.partner_keys (api_key, partner_id) "
                      "VALUES ('<key>', 'demo_001');")

        print()
        print("--- Pricing coefficients (vendor seed, idempotent) ---")
        from jobs.seed_coefficients import _insert_seeds  # vendor, on sys.path

        inserted = await _insert_seeds(conn, "postgres")
        total = await conn.fetchval("SELECT COUNT(*) FROM public.pricing_coefficients")
        print(f"  {inserted} new row(s); {total} total")

        print()
        print("--- Reference prices (anchors) ---")
        for category, tier, source, p25, p50, p75 in ANCHOR_SEEDS:
            await conn.execute(
                "INSERT INTO public.reference_prices "
                "(category, region, tier, source, p25, p50, p75, sample_count, updated_at) "
                "VALUES ($1, 'US', $2, $3, $4, $5, $6, 25, NOW()) "
                "ON CONFLICT DO NOTHING",
                category, tier, source, p25, p50, p75,
            )
        anchors = await conn.fetchval("SELECT COUNT(*) FROM public.reference_prices")
        print(f"  {anchors} anchor row(s) present")

        print()
        print("Demo seed complete.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(_seed())
