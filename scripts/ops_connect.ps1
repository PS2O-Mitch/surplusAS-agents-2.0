# Connect to the SurplusAS Cloud SQL instance as your IAM identity.
#
# Usage:
#   .\scripts\ops_connect.ps1                  # surplusas DB on default port
#   .\scripts\ops_connect.ps1 -Db postgres     # different DB
#   .\scripts\ops_connect.ps1 -Port 16432      # different local port
#
# Prereqs (one-time):
#   - `gcloud auth application-default login` (proxy uses ADC to mint tokens)
#   - Your @-style address is registered as a CLOUD_IAM_USER on the instance
#   - Your project IAM has roles/cloudsql.instanceUser + roles/cloudsql.client
#   - You've been added to the ops_reader DB role (scripts/grant_ops_reader.sql)
#
# What this script does NOT do: schema changes or destructive ops. For those,
# run scripts/apply_schema.py as surplusas_app (password from Secret Manager).

[CmdletBinding()]
param(
    [string]$Db   = 'surplusas',
    [int]   $Port = 15432
)

$ErrorActionPreference = 'Stop'
$Instance = 'ps2o-surplusas-api:us-central1:surplusas-db'

foreach ($cmd in @('cloud-sql-proxy', 'psql', 'gcloud')) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Error "$cmd not on PATH."
    }
}

$UserEmail = (& gcloud config get-value account 2>$null)
if ([string]::IsNullOrWhiteSpace($UserEmail) -or $UserEmail -eq '(unset)') {
    Write-Error "no gcloud account set. Run 'gcloud auth login' first."
}

$ProxyOut = [System.IO.Path]::GetTempFileName()
$ProxyErr = [System.IO.Path]::GetTempFileName()
Write-Host "→ proxy log: $ProxyErr (stderr), $ProxyOut (stdout)"
Write-Host "→ starting proxy on 127.0.0.1:$Port for $Instance"

$proxy = Start-Process -FilePath 'cloud-sql-proxy' `
    -ArgumentList '--auto-iam-authn',"--port=$Port",$Instance `
    -RedirectStandardOutput $ProxyOut `
    -RedirectStandardError  $ProxyErr `
    -NoNewWindow -PassThru

function Test-PortOpen([int]$p) {
    $tcp = New-Object System.Net.Sockets.TcpClient
    try   { $tcp.Connect('127.0.0.1', $p); return $true }
    catch { return $false }
    finally { $tcp.Close() }
}

try {
    $ready = $false
    for ($i = 0; $i -lt 50; $i++) {
        if (Test-PortOpen $Port) { $ready = $true; break }
        Start-Sleep -Milliseconds 200
    }

    if (-not $ready) {
        Write-Host "ERROR: proxy did not become ready. Last stderr lines:"
        if (Test-Path $ProxyErr) { Get-Content $ProxyErr -Tail 20 }
        exit 1
    }

    Write-Host "→ connecting as $UserEmail to $Db"
    & psql -h 127.0.0.1 -p $Port -U $UserEmail -d $Db
}
finally {
    if ($proxy -and -not $proxy.HasExited) {
        $proxy | Stop-Process -Force
    }
}
