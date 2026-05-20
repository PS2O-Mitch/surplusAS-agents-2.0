# Cloud Build triggers (Phase 7 M5).
#
# One trigger per per-agent pipeline, with path filters so a push under
# `agents/pricing/**` only rebuilds Pricing — not all five agents.
#
# Provisioning requires the Cloud Build GitHub App to be installed on
# the surplusAS-agents-2.0 repo (out-of-band, via Console). Pass the
# installation id via `var.github_app_installation_id`; if blank, no
# triggers are created. This makes the rest of the Terraform state
# applyable even before the GitHub connection lands.
#
# Per-pipeline runtime service accounts are intentionally NOT created
# here yet — Cloud Build runs as the project's default Cloud Build SA
# (already granted secretmanager.secretAccessor on github-submodule-pat
# in cloudbuild_secrets.tf). Tighten to a per-pipeline SA in a future
# phase once the IAM cost is justified.

locals {
  agents_with_pipelines = {
    pricing = {
      yaml = "infra/cloudbuild/pricing.cloudbuild.yaml"
      included = [
        "agents/pricing/**",
        "shared/**",
        "evals/golden/pricing.jsonl",
        "infra/cloudbuild/pricing.cloudbuild.yaml",
      ]
    }
    onboarding = {
      yaml = "infra/cloudbuild/onboarding.cloudbuild.yaml"
      included = [
        "agents/onboarding/**",
        "shared/**",
        "evals/golden/onboarding.jsonl",
        "infra/cloudbuild/onboarding.cloudbuild.yaml",
      ]
    }
  }

  enable_triggers = var.github_app_installation_id != ""
}

resource "google_cloudbuild_trigger" "agent" {
  for_each = local.enable_triggers ? local.agents_with_pipelines : {}

  project     = var.project_id
  location    = var.region
  name        = "${each.key}-agent-deploy"
  description = "Deploy the ${each.key} agent to Vertex Agent Engine on push to main with changes under its scope."

  github {
    owner = "PS2O-Mitch"
    name  = "surplusAS-agents-2.0"
    push {
      branch = "^main$"
    }
  }

  filename = each.value.yaml

  # Path-based filter: trigger only when one of these globs matches
  # something in the diff. Cloud Build uses gitglob-style patterns.
  included_files = each.value.included
}
