#!/usr/bin/env bash
# scripts/shoot_demo.sh
# Demo shoot helper — reset agents tables then open the static demo UI.
#
# Prereqs:
#   1. `gcloud auth application-default login` (ADC fresh)
#   2. PG_USER + PG_PASSWORD exported for the schema-owner role (typically
#      surplusas_app; password in Secret Manager db-app-password)
#   3. The gateway is running locally on http://localhost:8080
#      (`uv run python -m service.main`) OR pass a Cloud Run URL.
#
# Usage:
#   PG_USER=surplusas_app PG_PASSWORD='...' ./scripts/shoot_demo.sh
#   ./scripts/shoot_demo.sh https://surplusas-agents-XXX.run.app

set -euo pipefail

BASE_URL="${1:-http://localhost:8080}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo
echo "=== Step 1/2: reset demo state ==="
( cd "$REPO_ROOT" && uv run python scripts/seed_demo_merchant.py )

DEMO_URL="${BASE_URL}/static/surplusas-merchant-demo.html"
echo
echo "=== Step 2/2: open demo UI ==="
echo "URL: $DEMO_URL"

if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$DEMO_URL" >/dev/null 2>&1 || true
elif command -v open >/dev/null 2>&1; then
  open "$DEMO_URL" || true
else
  echo "(no open/xdg-open available — paste URL into a browser)"
fi

echo
echo "Beat 1: paste a merchant draft, generate listing, publish."
echo "Beat 2: 'Later that day - dispute the price' panel appears after publish."
echo "Re-run this script between takes for a clean slate."
