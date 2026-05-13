#!/usr/bin/env bash
# Connect to the SurplusAS Cloud SQL instance as your IAM identity.
#
# Usage:
#   scripts/ops_connect.sh                 # connects to surplusas as your gcloud account
#   scripts/ops_connect.sh --db postgres   # different DB
#   scripts/ops_connect.sh --port 16432    # different local port (default 15432)
#
# Prereqs (one-time):
#   - `gcloud auth application-default login` (proxy uses ADC to mint tokens)
#   - Your @-style address is registered as a CLOUD_IAM_USER on the instance
#   - Your project IAM has roles/cloudsql.instanceUser + roles/cloudsql.client
#   - You've been added to the ops_reader DB role (scripts/grant_ops_reader.sql)
#
# What this script does NOT do: schema changes or destructive ops. For those,
# run scripts/apply_schema.py as surplusas_app (password from Secret Manager).

set -euo pipefail

INSTANCE="ps2o-surplusas-api:us-central1:surplusas-db"
DB="surplusas"
PORT="15432"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --db)   DB="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

command -v cloud-sql-proxy >/dev/null || {
  echo "ERROR: cloud-sql-proxy not on PATH." >&2
  echo "  Install: https://cloud.google.com/sql/docs/postgres/sql-proxy#install" >&2
  exit 1
}
command -v psql >/dev/null || {
  echo "ERROR: psql not on PATH. Install Postgres client tools." >&2
  exit 1
}

USER_EMAIL="$(gcloud config get-value account 2>/dev/null || true)"
if [[ -z "$USER_EMAIL" || "$USER_EMAIL" == "(unset)" ]]; then
  echo "ERROR: no gcloud account set. Run 'gcloud auth login' first." >&2
  exit 1
fi

PROXY_LOG="$(mktemp -t cloud-sql-proxy.XXXXXX 2>/dev/null || mktemp)"
echo "→ proxy log: $PROXY_LOG"
echo "→ starting proxy on 127.0.0.1:$PORT for $INSTANCE"

cloud-sql-proxy --auto-iam-authn --port "$PORT" "$INSTANCE" \
  >"$PROXY_LOG" 2>&1 &
PROXY_PID=$!

cleanup() {
  if kill -0 "$PROXY_PID" 2>/dev/null; then
    kill "$PROXY_PID" 2>/dev/null || true
    wait "$PROXY_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# Wait for the port to accept TCP connections (≤10s).
ready=false
for _ in $(seq 1 50); do
  if (echo > "/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
    ready=true; break
  fi
  sleep 0.2
done

if ! $ready; then
  echo "ERROR: proxy did not become ready. Last log lines:" >&2
  tail -n 20 "$PROXY_LOG" >&2
  exit 1
fi

echo "→ connecting as $USER_EMAIL to $DB"
psql -h 127.0.0.1 -p "$PORT" -U "$USER_EMAIL" -d "$DB"
