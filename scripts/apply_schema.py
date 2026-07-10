"""Apply shared/db_schema.sql over a plain DSN.

Run with the OWNER dsn (Supabase `postgres` role), not the app role — the
schema + tables must be owned by a role that can later GRANT on them.

Usage:
    DATABASE_URL='postgresql://postgres:...@db.<ref>.supabase.co:5432/postgres' \
        uv run python scripts/apply_schema.py

The script is idempotent: every CREATE in db_schema.sql uses IF NOT EXISTS.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "shared" / "db_schema.sql"


def _dsn() -> str:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        sys.stderr.write("ERROR: DATABASE_URL must be exported.\n")
        sys.exit(1)
    return dsn


async def _apply() -> None:
    if not SCHEMA_PATH.is_file():
        sys.stderr.write(f"ERROR: schema file not found at {SCHEMA_PATH}\n")
        sys.exit(1)

    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    print(f"Applying {SCHEMA_PATH.name}...")

    conn = await asyncpg.connect(_dsn())
    try:
        # asyncpg executes multi-statement scripts as one batch when
        # there are no parameters.
        await conn.execute(sql)
    finally:
        await conn.close()

    print("Schema apply complete.")
    print()
    print("Verify with:")
    print(
        "  DATABASE_URL=... uv run python -c \"import asyncio; "
        "from scripts.apply_schema import _list_tables; asyncio.run(_list_tables())\""
    )


async def _list_tables() -> None:
    """Helper: list every table in the agents schema. Useful post-apply check."""
    conn = await asyncpg.connect(_dsn())
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


if __name__ == "__main__":
    asyncio.run(_apply())
