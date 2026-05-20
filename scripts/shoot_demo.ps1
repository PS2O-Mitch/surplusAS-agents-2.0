# scripts/shoot_demo.ps1
# Demo shoot helper — reset agents tables then open the static demo UI.
#
# Prereqs:
#   1. `gcloud auth application-default login` (ADC fresh)
#   2. `$env:PG_USER` + `$env:PG_PASSWORD` set for the schema-owner role
#      (typically surplusas_app; password in Secret Manager db-app-password)
#   3. The gateway is running locally on http://localhost:8080
#      (`uv run python -m service.main`) OR you have the Cloud Run URL
#      to open instead.
#
# Usage:
#   $env:PG_USER='surplusas_app'; $env:PG_PASSWORD='...'
#   .\scripts\shoot_demo.ps1
#
#   .\scripts\shoot_demo.ps1 -BaseUrl https://surplusas-agents-XXX.run.app

param(
  [string]$BaseUrl = "http://localhost:8080"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot

Write-Host ""
Write-Host "=== Step 1/2: reset demo state ==="
Push-Location $repoRoot
try {
  uv run python scripts/seed_demo_merchant.py
} finally {
  Pop-Location
}

$demoUrl = "$BaseUrl/static/surplusas-merchant-demo.html"
Write-Host ""
Write-Host "=== Step 2/2: open demo UI ==="
Write-Host "URL: $demoUrl"
Start-Process $demoUrl

Write-Host ""
Write-Host "Beat 1: paste a merchant draft, generate listing, publish."
Write-Host "Beat 2: 'Later that day - dispute the price' panel appears after publish."
Write-Host "Re-run this script between takes for a clean slate."
