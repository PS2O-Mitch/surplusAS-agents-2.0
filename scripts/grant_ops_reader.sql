-- Add a Cloud SQL IAM principal to the ops_reader role (read-only DB access).
--
-- Prereqs:
--   1. scripts/grant_agents_app.sql has been run (defines ops_reader).
--   2. The user has been provisioned in Cloud SQL as a CLOUD_IAM_USER:
--        gcloud sql users create <email> \
--          --instance=surplusas-db --type=cloud_iam_user \
--          --project=ps2o-surplusas-api
--   3. The user has roles/cloudsql.instanceUser + roles/cloudsql.client on
--      the project.
--
-- Run as surplusas_app (the schema owner), e.g.:
--   PGPASSWORD="$(gcloud secrets versions access latest --secret=db-app-password \
--                  --project=ps2o-surplusas-api)" \
--     psql "host=127.0.0.1 port=15432 user=surplusas_app dbname=surplusas" \
--         -v user_email='alice@example.com' \
--         -f scripts/grant_ops_reader.sql
--
-- Service-account principals: pass the service-account email *without* the
-- .gserviceaccount.com suffix — that's the Cloud SQL convention.

\set ON_ERROR_STOP on

\if :{?user_email}
\else
  \warn 'ERROR: pass -v user_email=<email>'
  \quit
\endif

GRANT ops_reader TO :"user_email";

\echo 'Granted ops_reader to' :'user_email'
