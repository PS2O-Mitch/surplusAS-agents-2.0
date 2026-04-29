# Six service accounts: one for the FastAPI gateway (Cloud Run), one per agent.
# Project-level role bindings live here. Per-resource invoker grants
# (run.invoker on the gateway, aiplatform.user on each Agent Engine resource)
# land in cloud_run.tf / agent_engine.tf as those resources are added.

locals {
  agents = {
    concierge       = { display = "Concierge agent (root, gemini-2.5-pro)" }
    pricing         = { display = "Pricing agent (gemini-2.5-flash)" }
    onboarding      = { display = "Onboarding agent (gemini-2.5-flash)" }
    listing_intake  = { display = "Listing Intake agent (gemini-2.5-flash)" }
    dispute_triage  = { display = "Dispute Triage agent (gemini-2.5-pro)" }
  }
}

# ---------------------------------------------------------------------------
# Gateway SA — fronts the public REST + webhook surface, runs on Cloud Run.
# ---------------------------------------------------------------------------
resource "google_service_account" "gateway" {
  project      = var.project_id
  account_id   = "gateway-sa"
  display_name = "surplusAS-agents gateway (Cloud Run)"
  description  = "FastAPI gateway: partner_keys auth, REST surface, webhook dispatch."
}

# ---------------------------------------------------------------------------
# One SA per agent. Agent Engine assumes this identity at invocation time.
# ---------------------------------------------------------------------------
resource "google_service_account" "agent" {
  for_each     = local.agents
  project      = var.project_id
  account_id   = "${replace(each.key, "_", "-")}-agent-sa"
  display_name = "surplusAS-agents ${each.key}"
  description  = each.value.display
}

# ---------------------------------------------------------------------------
# Project-level role bindings.
#
# All five agent SAs + the gateway SA need:
#   - roles/cloudsql.client    (asyncpg via Cloud SQL Python Connector)
#   - roles/secretmanager.secretAccessor (DB password, webhook signing key)
#   - roles/logging.logWriter  (structlog → Cloud Logging)
#   - roles/cloudtrace.agent   (OpenTelemetry → Cloud Trace)
#
# Agents only also get:
#   - roles/aiplatform.user    (Gemini calls + Agent Engine inter-agent reach)
# ---------------------------------------------------------------------------

locals {
  all_principals = merge(
    { gateway = google_service_account.gateway.email },
    { for k, sa in google_service_account.agent : k => sa.email },
  )

  shared_roles = [
    "roles/cloudsql.client",
    "roles/secretmanager.secretAccessor",
    "roles/logging.logWriter",
    "roles/cloudtrace.agent",
  ]

  # Cartesian: every principal × every shared role.
  shared_bindings = {
    for pair in setproduct(keys(local.all_principals), local.shared_roles) :
    "${pair[0]}:${pair[1]}" => { principal_key = pair[0], role = pair[1] }
  }
}

resource "google_project_iam_member" "shared" {
  for_each = local.shared_bindings

  project = var.project_id
  role    = each.value.role
  member  = "serviceAccount:${local.all_principals[each.value.principal_key]}"
}

resource "google_project_iam_member" "agent_aiplatform_user" {
  for_each = google_service_account.agent

  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${each.value.email}"
}

# ---------------------------------------------------------------------------
# Service-account-token-creator on each agent SA, granted to the deployer
# (gateway) so it can mint ID tokens scoped to peer audiences. The plain
# user identity that runs `terraform apply` does NOT need this; production
# A2A traffic is gateway → agent and agent → agent only.
# ---------------------------------------------------------------------------
resource "google_service_account_iam_member" "gateway_can_impersonate_agents" {
  for_each = google_service_account.agent

  service_account_id = each.value.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.gateway.email}"
}

# Listing Intake → Pricing and Dispute Triage → Pricing lateral A2A edges:
# both lateral callers must be able to mint ID tokens for the Pricing SA's audience.
resource "google_service_account_iam_member" "lateral_callers_can_impersonate_pricing" {
  for_each = toset(["listing_intake", "dispute_triage"])

  service_account_id = google_service_account.agent["pricing"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.agent[each.key].email}"
}

# Concierge → all four specialists (hub-and-spoke).
resource "google_service_account_iam_member" "concierge_can_impersonate_specialists" {
  for_each = toset(["pricing", "onboarding", "listing_intake", "dispute_triage"])

  service_account_id = google_service_account.agent[each.key].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.agent["concierge"].email}"
}
