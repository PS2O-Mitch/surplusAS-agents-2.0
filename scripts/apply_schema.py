"""Apply shared/db_schema.sql to the existing Cloud SQL surplusas-db.

Uses the same `cloud-sql-python-connector` + asyncpg path the agents use at
runtime, so this script doubles as a smoke test that the connector + ADC
work from a developer laptop.

Prereqs:
    gcloud auth application-default login
    PG_USER, PG_PASSWORD exported in the environment

Usage:
    PG_USER=postgres PG_PASSWORD='...' uv run python scripts/apply_schema.py

The script is idempotent: every CREATE in db_schema.sql uses IF NOT EXISTS.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
from google.cloud.sql.connector import Connector, IPTypes

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "ps2o-surplusas-api")
INSTANCE = os.environ.get(
    "CLOUD_SQL_INSTANCE",
    "ps2o-surplusas-api:us-central1:surplusas-db",
)
DB_NAME = os.environ.get("DB_NAME", "surplusas")
PG_USER = os.environ.get("PG_USER")
PG_PASSWORD = os.environ.get("PG_PASSWORD")

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "shared" / "db_schema.sql"


async def _apply() -> None:
    if not PG_USER or not PG_PASSWORD:
        sys.stderr.write("ERROR: PG_USER and PG_PASSWORD must be exported.\n")
        sys.exit(1)

    if not SCHEMA_PATH.is_file():
        sys.stderr.write(f"ERROR: schema file not found at {SCHEMA_PATH}\n")
        sys.exit(1)

    sql = SCHEMA_PATH.read_text(encoding="utf-8")

    print(f"Applying {SCHEMA_PATH.name} to {INSTANCE}/{DB_NAME} as {PG_USER}...")

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
            # asyncpg executes multi-statement scripts as one batch when
            # there are no parameters and no transaction wrap is needed.
            await conn.execute(sql)
        finally:
            await conn.close()
    finally:
        await connector.close_async()

    print("Schema apply complete.")
    print()
    print("Verify with:")
    print(
        "  PG_USER=$PG_USER PG_PASSWORD=$PG_PASSWORD "
        "uv run python -c \"import asyncio; "
        "from scripts.apply_schema import _list_tables; asyncio.run(_list_tables())\""
    )


async def _list_tables() -> None:
    """Helper: list every table in the agents schema. Useful post-apply check."""
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
            rows = await conn.fetch(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'agents' ORDER BY table_name"
            )
            print(f"Tables in agents schema ({len(rows)}):")
            for r in rows:
                print(f"  - {r['table_name']}")
        finally:
            await conn.close()
    finally:
        await connector.close_async()


if __name__ == "__main__":
    asyncio.run(_apply())
