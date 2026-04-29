# Adds the agents application user to the EXISTING `surplusas-db` Cloud SQL
# instance. We deliberately do NOT manage the instance itself here — that
# belongs to surplusAS-API-2.0's Terraform. Schema-level GRANTs on
# `agents.*` and read on `public.{pricing_coefficients,reference_prices}` run
# as a one-shot SQL after the user lands; see scripts/grant_agents_app.sql.

data "google_sql_database_instance" "surplusas" {
  project = var.project_id
  name    = var.cloud_sql_instance_name
}

resource "google_sql_user" "agents_app" {
  project  = var.project_id
  instance = data.google_sql_database_instance.surplusas.name
  name     = var.agents_app_db_user
  password = var.agents_app_db_password
  type     = "BUILT_IN"
}
