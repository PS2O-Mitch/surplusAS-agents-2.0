-- Provision a fresh Supabase Postgres for surplusAS-agents-2.0.
--
-- Run as the `postgres` role (Supabase SQL editor, or psql with the owner
-- DSN). Full standup order:
--   1. vendor/surplusas-pricing/sql/001_reference_prices.sql
--   2. vendor/surplusas-pricing/sql/002_pricing_coefficients.sql
--   3. Section A below (role + partner_keys)
--   4. shared/db_schema.sql  (DATABASE_URL=<owner-dsn> uv run python scripts/apply_schema.py)
--   5. Section B below (grants — needs the tables from 1/2/4 to exist)
--   6. DATABASE_URL=<owner-dsn> uv run python scripts/seed_demo_merchant.py
--
-- ponytail: ops_reader role dropped with Cloud SQL IAM; the Supabase
-- dashboard (postgres role) is the ops path now.

-- ── Section A: app role + partner_keys ─────────────────────────────────

-- Replace REPLACE_WITH_APP_PASSWORD before running. The same password goes
-- into the app's DATABASE_URL (Fly secret / local .env).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'surplusas_agents_app') THEN
        CREATE ROLE surplusas_agents_app LOGIN PASSWORD 'REPLACE_WITH_APP_PASSWORD';
    END IF;
END$$;

-- public.partner_keys has no DDL in any owning repo (it belonged to the
-- retired SurplusAS-API-2.0 monolith), so it is authored here. Shape from
-- the readers: shared/auth.py (api_key, partner_id, context_json) and
-- scripts/seed_demo_merchant.py (active).
-- NOTE: shared/auth.py does not filter on `active` today — deactivating a
-- key does not revoke access. Pre-existing behavior, kept as-is.
CREATE TABLE IF NOT EXISTS public.partner_keys (
    api_key      TEXT PRIMARY KEY,
    partner_id   TEXT NOT NULL,
    context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Section B: grants (run after ALL tables exist) ─────────────────────

GRANT USAGE ON SCHEMA agents TO surplusas_agents_app;
GRANT USAGE ON SCHEMA public TO surplusas_agents_app;

GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA agents
    TO surplusas_agents_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA agents
    GRANT SELECT, INSERT, UPDATE ON TABLES TO surplusas_agents_app;

-- Append-only enforcement (CLAUDE.md guardrail #3): agents.recommendation_log
-- is INSERT-only. Re-derivations write a NEW row with replay_of set; existing
-- audit rows are NEVER mutated or removed. Enforced at the DB level so the app
-- role physically cannot UPDATE/DELETE an audit row. (disputes still needs
-- UPDATE for the PATCH /v1/disputes resolution path, so the revoke is scoped
-- to recommendation_log alone.)
REVOKE UPDATE, DELETE ON agents.recommendation_log FROM surplusas_agents_app;

GRANT SELECT ON public.partner_keys           TO surplusas_agents_app;
GRANT SELECT ON public.pricing_coefficients   TO surplusas_agents_app;
GRANT SELECT ON public.reference_prices       TO surplusas_agents_app;
