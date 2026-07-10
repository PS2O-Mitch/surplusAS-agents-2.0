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

GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA agents TO surplusas_agents_app;

-- Default privileges deliberately EXCLUDE UPDATE: a dropped-and-recreated
-- agents.recommendation_log must come back INSERT-only (guardrail #3), not
-- silently inherit UPDATE. Tables with a legitimate mutate path get UPDATE
-- explicitly below — extend this list when a new mutable table lands.
ALTER DEFAULT PRIVILEGES IN SCHEMA agents
    GRANT SELECT, INSERT ON TABLES TO surplusas_agents_app;

-- Mutable tables (verified against the code's UPDATE statements):
--   merchant_profiles      — onboarding amendments
--   disputes               — PATCH /v1/disputes resolution
--   webhook_deliveries     — retry sweep bumps attempt/delivered_at
--   webhook_subscriptions  — unsubscribe sets active=FALSE
GRANT UPDATE ON agents.merchant_profiles,
                agents.disputes,
                agents.webhook_deliveries,
                agents.webhook_subscriptions
    TO surplusas_agents_app;

-- Append-only enforcement (CLAUDE.md guardrail #3): agents.recommendation_log
-- is INSERT-only. Re-derivations write a NEW row with replay_of set; existing
-- audit rows are NEVER mutated or removed. The narrowed grants above never
-- hand out UPDATE/DELETE on it; the REVOKE is belt-and-braces for databases
-- provisioned under the older broad grant.
REVOKE UPDATE, DELETE ON agents.recommendation_log FROM surplusas_agents_app;

GRANT SELECT ON public.partner_keys           TO surplusas_agents_app;
GRANT SELECT ON public.pricing_coefficients   TO surplusas_agents_app;
GRANT SELECT ON public.reference_prices       TO surplusas_agents_app;

-- ── Section C: lock the public schema out of Supabase's Data API ───────
-- Supabase exposes the `public` schema over PostgREST and grants new
-- tables to the `anon`/`authenticated` roles by default. partner_keys is
-- the bearer-credential store — anon read = full gateway auth bypass; and
-- writable reference tables would let anyone poison pricing anchors.
ALTER TABLE public.partner_keys         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.pricing_coefficients ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reference_prices     ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.partner_keys, public.pricing_coefficients, public.reference_prices
    FROM anon, authenticated;

-- RLS applies to surplusas_agents_app too (it has no BYPASSRLS), so give it
-- explicit all-rows read policies. anon/authenticated get no policies →
-- denied even if a future default grant reappears. The postgres owner
-- (provisioning + seed scripts) bypasses RLS on its own tables.
DROP POLICY IF EXISTS app_read ON public.partner_keys;
CREATE POLICY app_read ON public.partner_keys
    FOR SELECT TO surplusas_agents_app USING (true);
DROP POLICY IF EXISTS app_read ON public.pricing_coefficients;
CREATE POLICY app_read ON public.pricing_coefficients
    FOR SELECT TO surplusas_agents_app USING (true);
DROP POLICY IF EXISTS app_read ON public.reference_prices;
CREATE POLICY app_read ON public.reference_prices
    FOR SELECT TO surplusas_agents_app USING (true);
