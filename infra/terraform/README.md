# Terraform — surplusAS-agents-2.0

Infrastructure-as-code for the contest entry. Scope of this directory is
**identity, secrets, and the agents-app DB user only** in the Week 1 cut.
Agent Engine resources, the Cloud Run gateway, and Cloud Build triggers
arrive in later weeks as the runtime targets stabilise.

## What's here (Week 1)

| File                       | Purpose                                                                                                  |
| -------------------------- | -------------------------------------------------------------------------------------------------------- |
| `versions.tf`              | Provider pins (`google` / `google-beta` ~> 6.10, terraform >= 1.9.0).                                    |
| `backend.tf`               | GCS-backed state. Bucket bootstrapped manually (see comments inside).                                    |
| `main.tf`                  | Provider config + project-API enablement.                                                                |
| `variables.tf`             | Project, region, Cloud SQL instance/db names, secrets.                                                   |
| `iam.tf`                   | 6 service accounts (`gateway-sa`, 5 `<agent>-agent-sa`), shared role bindings, A2A impersonation chain.  |
| `secret_manager.tf`        | `db-password-agents` and `webhook-signing-key` secrets + per-SA accessor grants.                         |
| `cloudbuild_secrets.tf`    | `github-submodule-pat` secret for Cloud Build to fetch the private `vendor/surplusas-pricing` submodule. |
| `cloud_sql.tf`             | `surplusas_agents_app` user on the **existing** `surplusas-db` instance (does NOT create the instance).  |
| `outputs.tf`               | SA emails, secret ids, Cloud SQL connection name.                                                        |
| `terraform.tfvars.example` | Template; copy to `terraform.tfvars` and fill in (not committed).                                        |

## What's NOT here yet

- `agent_engine.tf` — `google_vertex_ai_*` Agent Engine deployment resources. Adding when the Vertex AI provider surface stabilises (Week 2 verification).
- `cloud_run.tf` — gateway service deployment. Week 3.
- `cloud_build.tf` — six per-service triggers. Week 3.

## Bootstrap

```bash
# 1. Create the state bucket (one-shot, manual)
# Note: If using PowerShell on Windows, use ` instead of \ for line continuation.
gcloud storage buckets create gs://ps2o-surplusas-tf-state `
  --project=ps2o-surplusas-api `
  --location=us-central1 `
  --uniform-bucket-level-access
gcloud storage buckets update gs://ps2o-surplusas-tf-state --versioning

# 2. Authenticate locally
gcloud auth application-default login

# 3. Init backend
cd infra/terraform
terraform init `
  -backend-config="bucket=ps2o-surplusas-tf-state" `
  -backend-config="prefix=agents/"

# 4. Fill in tfvars
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars — set strong random values for the two secrets

# 5. Plan and apply
terraform plan
terraform apply
```

## After apply

Run the schema bootstrap against the Cloud SQL instance:

```bash
# Connect with the Cloud SQL Auth Proxy or `gcloud sql connect`
# Bash:
# gcloud sql connect surplusas-db --user=postgres --database=surplusas \
#   --project=ps2o-surplusas-api < ../../shared/db_schema.sql
# PowerShell:
Get-Content ../../shared/db_schema.sql | gcloud sql connect surplusas-db --user=postgres --database=surplusas `
  --project=ps2o-surplusas-api
```

Then grant the `surplusas_agents_app` role appropriate access:

```sql
GRANT USAGE  ON SCHEMA agents       TO surplusas_agents_app;
GRANT USAGE  ON SCHEMA public       TO surplusas_agents_app;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA agents TO surplusas_agents_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA agents
    GRANT SELECT, INSERT, UPDATE ON TABLES TO surplusas_agents_app;
GRANT SELECT ON public.partner_keys, public.pricing_coefficients, public.reference_prices
    TO surplusas_agents_app;
```

## Cloud Build submodule PAT

The Cloud Build pipelines for the Pricing and Onboarding agents fetch the
private `vendor/surplusas-pricing` submodule using a GitHub fine-grained
PAT (scoped to read `PS2O-Mitch/surplusAS-pricing-intel`). Terraform
creates the `github-submodule-pat` Secret Manager resource and grants the
project's default Cloud Build runtime SA (`<project_number>@cloudbuild.gserviceaccount.com`)
accessor on it; the pipelines pull it via `availableSecrets`. After
`terraform apply`, seed the secret value (the `REPLACE_ME` placeholder
seeded by Terraform is not a usable token):

```bash
# Paste the PAT (no trailing newline) — same value used for the
# GitHub Actions SUBMODULE_TOKEN secret.
printf 'ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' \
  | gcloud secrets versions add github-submodule-pat \
      --project=ps2o-surplusas-api \
      --data-file=-
```

Rotate the same way; Terraform tracks the secret resource, not the version.
