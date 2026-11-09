param(
    [string]$TunnelName = "vidyra"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$cf = Join-Path $root "vidyra-tools\cloudflared\cloudflared.exe"

$health = $null
try { $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 3 } catch {}
if (-not $health) {
    Write-Host "Backend not responding on :8000. Start it first."
    exit 1
}

$cred = Join-Path $HOME ".cloudflared\$TunnelName.json"
$cfg = Join-Path $PSScriptRoot "config.yml"

if (Test-Path $cfg) {
    & $cf tunnel run --config $cfg $TunnelName
} else {
    Write-Host "No config.yml yet - starting temporary quick tunnel (URL changes each start)."
    & $cf tunnel --url http://127.0.0.1:8000 --no-autoupdate
}