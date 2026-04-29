# Two secrets, both consumed by `envFromSecretManager` in the agent manifests.
# Initial values are seeded from Terraform variables; after first apply, rotate
# in-console (or `gcloud secrets versions add`) — Terraform tracks the
# resource, not the version.

resource "google_secret_manager_secret" "db_password_agents" {
  project   = var.project_id
  secret_id = "db-password-agents"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "db_password_agents_v1" {
  secret      = google_secret_manager_secret.db_password_agents.id
  secret_data = var.agents_app_db_password
}

resource "google_secret_manager_secret" "webhook_signing_key" {
  project   = var.project_id
  secret_id = "webhook-signing-key"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "webhook_signing_key_v1" {
  secret      = google_secret_manager_secret.webhook_signing_key.id
  secret_data = var.webhook_signing_key
}

# Per-secret accessor grants. The shared role binding in iam.tf already gives
# every SA project-wide secretmanager.secretAccessor; these resource-level
# grants are an extra layer of intent that lets us tighten the project-level
# binding later (drop it from gateway/specialists that don't need every secret).
resource "google_secret_manager_secret_iam_member" "db_password_access" {
  for_each = local.all_principals

  project   = var.project_id
  secret_id = google_secret_manager_secret.db_password_agents.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value}"
}

resource "google_secret_manager_secret_iam_member" "webhook_key_access" {
  for_each = toset(["gateway", "dispute_triage"])

  project   = var.project_id
  secret_id = google_secret_manager_secret.webhook_signing_key.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value == "gateway" ? google_service_account.gateway.email : google_service_account.agent[each.value].email}"
}
