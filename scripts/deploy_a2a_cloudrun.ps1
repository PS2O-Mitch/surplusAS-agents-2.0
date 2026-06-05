<#
.SYNOPSIS
  Deploy one agent's open A2A surface (service/a2a_app.py — ADK to_a2a) to Cloud Run.

.DESCRIPTION
  Builds the repo Dockerfile via Cloud Build (`--source .`), wires the agent's
  service account + Cloud SQL + DB secret, then sets A2A_PUBLIC_HOST so the
  published Agent Card advertises the live Cloud Run URL. PowerShell twin of
  scripts/deploy_a2a_cloudrun.sh.

.EXAMPLE
  ./scripts/deploy_a2a_cloudrun.ps1
  ./scripts/deploy_a2a_cloudrun.ps1 -Agent concierge
#>
param(
  [string]$Agent    = "pricing",
  [string]$Project  = "ps2o-surplusas-api",
  [string]$Region   = "us-central1",
  [string]$Sa       = "pricing-agent-sa@ps2o-surplusas-api.iam.gserviceaccount.com",
  [string]$CloudSql = "ps2o-surplusas-api:us-central1:surplusas-db"
)
$ErrorActionPreference = "Stop"
$Service = "surplusas-a2a-" + ($Agent -replace "_","-")

Write-Host ">> Deploying agent='$Agent' as Cloud Run service '$Service' (project=$Project, region=$Region)"

$envVars = @(
  "A2A_AGENT=$Agent",
  "A2A_PUBLIC_PROTOCOL=https",
  "A2A_PUBLIC_PORT=443",
  "GOOGLE_GENAI_USE_VERTEXAI=true",
  "GOOGLE_CLOUD_PROJECT=$Project",
  "GOOGLE_CLOUD_LOCATION=$Region",
  "CLOUD_SQL_INSTANCE=$CloudSql",
  "DB_NAME=surplusas",
  "DB_USER=surplusas_agents_app"
) -join ","

# 1) Build + deploy from source.
gcloud run deploy $Service `
  --source . `
  --project $Project `
  --region $Region `
  --service-account $Sa `
  --allow-unauthenticated `
  --add-cloudsql-instances $CloudSql `
  --memory 2Gi --cpu 2 --timeout 300 --port 8080 `
  --set-env-vars $envVars `
  --set-secrets "DB_PASSWORD=db-password-agents:latest"
if ($LASTEXITCODE -ne 0) { throw "gcloud run deploy failed" }

# 2) Re-point the Agent Card's advertised host at the assigned public URL.
$Url  = (gcloud run services describe $Service --project $Project --region $Region --format="value(status.url)")
$Host_ = $Url -replace "^https://",""
gcloud run services update $Service --project $Project --region $Region --update-env-vars "A2A_PUBLIC_HOST=$Host_"
if ($LASTEXITCODE -ne 0) { throw "gcloud run services update failed" }

Write-Host ""
Write-Host ">> A2A surface live:"
Write-Host "   Service URL : $Url"
Write-Host "   Agent Card  : $Url/.well-known/agent-card.json"
Write-Host "   JSON-RPC    : $Url/  (POST, application/json)"
