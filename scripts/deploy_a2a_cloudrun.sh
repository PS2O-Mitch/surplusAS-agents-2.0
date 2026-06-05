#!/usr/bin/env bash
#
# Deploy one agent's OPEN A2A surface (service/a2a_app.py — ADK to_a2a) to
# Cloud Run. Builds the repo Dockerfile via Cloud Build (`--source .`), wires
# the agent's service account + Cloud SQL + DB secret so a real message/send
# works, then sets A2A_PUBLIC_HOST so the published Agent Card advertises the
# live, reachable Cloud Run URL.
#
# Prereqs: gcloud authenticated with access to the target project; Cloud Run,
# Cloud Build, and Artifact Registry APIs enabled; the agent service account and
# the db-password-agents secret already provisioned (they are — see
# infra/terraform + scripts/deploy_agent.py SECRET_ID_MAP).
#
# Usage:
#   scripts/deploy_a2a_cloudrun.sh                # pricing (default)
#   scripts/deploy_a2a_cloudrun.sh concierge      # any agent in the mesh
#   PROJECT=... REGION=... SA=... scripts/deploy_a2a_cloudrun.sh pricing
#
set -euo pipefail

AGENT="${1:-pricing}"
PROJECT="${PROJECT:-ps2o-surplusas-api}"
REGION="${REGION:-us-central1}"
SA="${SA:-pricing-agent-sa@ps2o-surplusas-api.iam.gserviceaccount.com}"
CLOUD_SQL="${CLOUD_SQL:-ps2o-surplusas-api:us-central1:surplusas-db}"
SERVICE="surplusas-a2a-${AGENT//_/-}"

echo ">> Deploying agent='${AGENT}' as Cloud Run service '${SERVICE}' (project=${PROJECT}, region=${REGION})"

# 1) Build + deploy from source. Card discovery needs none of the DB/Vertex
#    wiring; it is included so a real JSON-RPC message/send can run the agent.
gcloud run deploy "${SERVICE}" \
  --source . \
  --project "${PROJECT}" \
  --region "${REGION}" \
  --service-account "${SA}" \
  --allow-unauthenticated \
  --add-cloudsql-instances "${CLOUD_SQL}" \
  --memory 2Gi --cpu 2 --timeout 300 --port 8080 \
  --set-env-vars "A2A_AGENT=${AGENT},A2A_PUBLIC_PROTOCOL=https,A2A_PUBLIC_PORT=443,GOOGLE_GENAI_USE_VERTEXAI=true,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${REGION},CLOUD_SQL_INSTANCE=${CLOUD_SQL},DB_NAME=surplusas,DB_USER=surplusas_agents_app" \
  --set-secrets "DB_PASSWORD=db-password-agents:latest"

# 2) Read the assigned public URL and re-point the Agent Card's advertised host
#    at it (two-pass: the URL isn't known until the service exists).
URL="$(gcloud run services describe "${SERVICE}" --project "${PROJECT}" --region "${REGION}" --format='value(status.url)')"
HOST="${URL#https://}"
gcloud run services update "${SERVICE}" \
  --project "${PROJECT}" --region "${REGION}" \
  --update-env-vars "A2A_PUBLIC_HOST=${HOST}"

echo
echo ">> A2A surface live:"
echo "   Service URL : ${URL}"
echo "   Agent Card  : ${URL}/.well-known/agent-card.json"
echo "   JSON-RPC    : ${URL}/  (POST, application/json)"
echo
echo ">> Verify discovery from anywhere:"
echo "   curl -s ${URL}/.well-known/agent-card.json | python -m json.tool"
