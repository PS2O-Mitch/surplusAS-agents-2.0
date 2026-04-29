output "gateway_sa_email" {
  description = "Service account that runs the FastAPI gateway on Cloud Run."
  value       = google_service_account.gateway.email
}

output "agent_sa_emails" {
  description = "Service-account email per agent (concierge, pricing, onboarding, listing_intake, dispute_triage)."
  value       = { for k, sa in google_service_account.agent : k => sa.email }
}

output "secret_db_password_id" {
  description = "Secret Manager resource id for the agents DB password."
  value       = google_secret_manager_secret.db_password_agents.id
}

output "secret_webhook_signing_key_id" {
  description = "Secret Manager resource id for the webhook HMAC signing key."
  value       = google_secret_manager_secret.webhook_signing_key.id
}

output "cloud_sql_connection_name" {
  description = "Cloud SQL connection name for the Cloud SQL Python Connector."
  value       = data.google_sql_database_instance.surplusas.connection_name
}

output "agents_app_db_user" {
  description = "Postgres role the agents authenticate as."
  value       = google_sql_user.agents_app.name
}
