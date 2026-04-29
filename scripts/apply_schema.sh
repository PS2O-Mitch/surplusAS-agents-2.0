#!/usr/bin/env bash
# Apply shared/db_schema.sql to the existing Cloud SQL surplusas-db.
#
# Prereqs:
#   - gcloud auth login + gcloud auth application-default login
#   - psql on PATH (gcloud sql connect bootstraps a Cloud SQL Proxy then execs psql)
#   - $PG_SUPERUSER_PASSWORD exported (the postgres role's password)
#
# Usage:
#   PG_SUPERUSER_PASSWORD='...' ./scripts/apply_schema.sh
#
# Idempotent: every CREATE in shared/db_schema.sql uses IF NOT EXISTS, so it's
# safe to re-run after schema additions in later weeks.

set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:-ps2o-surplusas-api}"
INSTANCE="${CLOUD_SQL_INSTANCE_NAME:-surplusas-db}"
DB_NAME="${DB_NAME:-surplusas}"
SUPERUSER="${PG_SUPERUSER:-postgres}"

if [[ -z "${PG_SUPERUSER_PASSWORD:-}" ]]; then
  echo "ERROR: PG_SUPERUSER_PASSWORD is not set." >&2
  echo "Export the postgres role's password before running this script." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA="${REPO_ROOT}/shared/db_schema.sql"

if [[ ! -f "$SCHEMA" ]]; then
  echo "ERROR: schema file not found at $SCHEMA" >&2
  exit 1
fi

echo "Applying $SCHEMA to ${PROJECT}:${INSTANCE}/${DB_NAME} as ${SUPERUSER}..."

PGPASSWORD="$PG_SUPERUSER_PASSWORD" \
  gcloud sql connect "$INSTANCE" \
    --user="$SUPERUSER" \
    --database="$DB_NAME" \
    --project="$PROJECT" \
    --quiet \
  < "$SCHEMA"

echo "Schema apply complete."
echo
echo "Next steps:"
echo "  1. Run terraform apply in infra/terraform/ to create surplusas_agents_app user."
echo "  2. Grant the app user appropriate access:"
echo "     GRANT USAGE ON SCHEMA agents, public TO surplusas_agents_app;"
echo "     GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA agents TO surplusas_agents_app;"
echo "     ALTER DEFAULT PRIVILEGES IN SCHEMA agents"
echo "       GRANT SELECT, INSERT, UPDATE ON TABLES TO surplusas_agents_app;"
echo "     GRANT SELECT ON public.partner_keys, public.pricing_coefficients, public.reference_prices"
echo "       TO surplusas_agents_app;"
