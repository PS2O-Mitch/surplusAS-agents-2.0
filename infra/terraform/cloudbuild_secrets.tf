# Cloud Build pipeline secrets.
#
# `github-submodule-pat` is a GitHub fine-grained PAT scoped to read the
# private `surplusAS-pricing-intel` repo. The Cloud Build pipelines for the
# Pricing and Onboarding agents pull the `vendor/surplusas-pricing` git
# submodule using this token via the `git config insteadOf` recipe (see
# `infra/cloudbuild/*.cloudbuild.yaml`). Mirrors the GitHub Actions
# `SUBMODULE_TOKEN` secret used by `.github/workflows/ci.yml`.
#
# Terraform creates the secret resource and seeds an initial version from
# `var.github_submodule_pat`. Rotate via `gcloud secrets versions add`
# (Terraform tracks the resource, not the version).

# Used to resolve the project number for the default Cloud Build runtime SA.
data "google_project" "current" {
  project_id = var.project_id
}

resource "google_secret_manager_secret" "github_submodule_pat" {
  project   = var.project_id
  secret_id = "github-submodule-pat"

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "github_submodule_pat_v1" {
  secret      = google_secret_manager_secret.github_submodule_pat.id
  secret_data = var.github_submodule_pat
}

# Grant the project's default Cloud Build runtime SA accessor on this secret.
# Triggers live in cloudbuild_triggers.tf (Phase 7 M5). They share the
# default Cloud Build SA today; per-pipeline SAs are a future tightening
# once the IAM cost is justified.
resource "google_secret_manager_secret_iam_member" "cloudbuild_can_read_submodule_pat" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.github_submodule_pat.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${data.google_project.current.number}@cloudbuild.gserviceaccount.com"
}
