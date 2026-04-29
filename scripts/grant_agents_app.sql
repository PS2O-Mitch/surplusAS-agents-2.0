-- Run AFTER terraform apply (which creates the surplusas_agents_app user)
-- AND AFTER scripts/apply_schema.sh has run (which creates schema agents + tables).
--
-- Connect as postgres superuser, then \i this file.

\set ON_ERROR_STOP on

GRANT USAGE ON SCHEMA agents TO surplusas_agents_app;
GRANT USAGE ON SCHEMA public TO surplusas_agents_app;

GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA agents
    TO surplusas_agents_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA agents
    GRANT SELECT, INSERT, UPDATE ON TABLES TO surplusas_agents_app;

-- Read-only on the cross-schema tables owned by surplusAS-API-2.0 / pricing-intel.
-- IF NOT EXISTS guards aren't valid for GRANT; if these tables don't exist yet
-- the grant will fail and you should skip until the owning repo has provisioned them.
GRANT SELECT ON public.partner_keys           TO surplusas_agents_app;
GRANT SELECT ON public.pricing_coefficients   TO surplusas_agents_app;
GRANT SELECT ON public.reference_prices       TO surplusas_agents_app;
