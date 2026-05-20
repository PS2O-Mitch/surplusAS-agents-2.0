variable "project_id" {
  type        = string
  description = "GCP project hosting all surplusAS-agents resources."
  default     = "ps2o-surplusas-api"
}

variable "region" {
  type        = string
  description = "Primary region for Agent Engine, Cloud Run, Cloud Build, and Cloud SQL."
  default     = "us-central1"
}

variable "cloud_sql_instance_name" {
  type        = string
  description = "Name of the existing surplusas Cloud SQL instance (NOT created here)."
  default     = "surplusas-db"
}

variable "cloud_sql_database_name" {
  type        = string
  description = "Name of the application database inside the Cloud SQL instance."
  default     = "surplusas"
}

variable "agents_app_db_user" {
  type        = string
  description = "Postgres role used by the agents (writes to schema agents)."
  default     = "surplusas_agents_app"
}

variable "agents_app_db_password" {
  type        = string
  description = "Initial password for agents_app_db_user. Stored in Secret Manager; rotate post-bootstrap."
  sensitive   = true
}

variable "webhook_signing_key" {
  type        = string
  description = "Initial HMAC signing key for outbound webhooks. Stored in Secret Manager; rotate before launch."
  sensitive   = true
}

variable "github_submodule_pat" {
  type        = string
  description = "GitHub fine-grained PAT with read access to surplusAS-pricing-intel. Used by Cloud Build pipelines to fetch the vendor/surplusas-pricing submodule. Stored in Secret Manager; rotate via gcloud."
  sensitive   = true
}

variable "alert_notification_channel_id" {
  type        = string
  description = "Full resource id of a pre-existing Cloud Monitoring notification channel (email or PagerDuty). Used by the webhook dead-letter alert in monitoring.tf. Format: projects/<id>/notificationChannels/<id>. Create channels via Cloud Console (email channels need interactive verification)."
}

variable "github_app_installation_id" {
  type        = string
  description = "Cloud Build GitHub App installation id (numeric). Created out-of-band by installing the Cloud Build GitHub App on the surplusAS-agents-2.0 repo and copying the installation id from the Cloud Console (Cloud Build > Triggers > Connect Repository). Required by cloudbuild_triggers.tf."
  default     = ""
}
