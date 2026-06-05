-- Run AFTER terraform apply (which creates the surplusas_agents_app user)
-- AND AFTER scripts/apply_schema.sh has run (which creates schema agents + tables).
--
-- Connect as surplusas_app (the schema owner; password in Secret Manager
-- secret db-app-password), then \i this file. The postgres role on Cloud SQL
-- is NOT a real superuser — it's a member of cloudsqlsuperuser but does not
-- own the agents schema, so it cannot grant on agents.* nor on the
-- public.{partner_keys,pricing_coefficients,reference_prices} tables that
-- surplusas_app owns.

\set ON_ERROR_STOP on

GRANT USAGE ON SCHEMA agents TO surplusas_agents_app;
GRANT USAGE ON SCHEMA public TO surplusas_agents_app;

GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA agents
    TO surplusas_agents_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA agents
    GRANT SELECT, INSERT, UPDATE ON TABLES TO surplusas_agents_app;

-- Append-only enforcement (CLAUDE.md guardrail #3): agents.recommendation_log
-- is INSERT-only. Re-derivations write a NEW row with replay_of set; existing
-- audit rows are NEVER mutated or removed. Enforce at the DB level so the app
-- role physically cannot UPDATE/DELETE an audit row — defense-in-depth beyond
-- the app-code convention. (The broad grant above hands the app role UPDATE on
-- every agents.* table; this claws it back for the audit log only. disputes
-- still needs UPDATE for the PATCH /v1/disputes resolution path, so the revoke
-- is scoped to recommendation_log alone.)
REVOKE UPDATE, DELETE ON agents.recommendation_log FROM surplusas_agents_app;

-- Read-only on the cross-schema tables owned by surplusAS-API-2.0 / pricing-intel.
-- IF NOT EXISTS guards aren't valid for GRANT; if these tables don't exist yet
-- the grant will fail and you should skip until the owning repo has provisioned them.
GRANT SELECT ON public.partner_keys           TO surplusas_agents_app;
GRANT SELECT ON public.pricing_coefficients   TO surplusas_agents_app;
GRANT SELECT ON public.reference_prices       TO surplusas_agents_app;

-- ── ops_reader role ────────────────────────────────────────────────────
-- Read-only group role for human operators (engineers, on-call) who
-- connect as their own Cloud SQL IAM user via cloud-sql-proxy
-- --auto-iam-authn. Add individual users to this role with
-- scripts/grant_ops_reader.sql. Off-boarding = revoke project IAM; the
-- DB-level membership becomes inert.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ops_reader') THEN
        CREATE ROLE ops_reader;
    END IF;
END$$;

GRANT USAGE ON SCHEMA agents TO ops_reader;
GRANT USAGE ON SCHEMA public TO ops_reader;

GRANT SELECT ON ALL TABLES IN SCHEMA agents TO ops_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA agents
    GRANT SELECT ON TABLES TO ops_reader;

GRANT SELECT ON public.partner_keys           TO ops_reader;
GRANT SELECT ON public.pricing_coefficients   TO ops_reader;
GRANT SELECT ON public.reference_prices       TO ops_reader;
