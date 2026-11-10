$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$py = Join-Path $root ".venv\Scripts\python.exe"
$cf = Join-Path $root "vidyra-tools\cloudflared\cloudflared.exe"
$backlog = Join-Path $root "deploy\tunnel\backend.log"
$tunlog = Join-Path $root "deploy\tunnel\tunnel.log"
$tunerr = Join-Path $root "deploy\tunnel\tunnel.err.log"

function Start-Backend {
    $env:APP_ENV = "development"
    $env:CORS_ORIGINS = "http://localhost:5173,http://localhost:4173,https://vidyra.web.app"
    $env:FIREBASE_PROJECT_ID = "vidyra"
    $env:SERVICE_ACCOUNT_PATH = Join-Path $root "backend\config\vidyra-firebase-adminsdk-fbsvc-31c93a2e64.json"
    # R2_ACCOUNT_ID / R2_BUCKET / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY are required.
    $env:STORAGE_BACKEND = "r2"
    $env:STORAGE_ROOT = "storage\cache"
    $env:WORK_ROOT = "storage\work"
    # Solo RunPod: override with $env:PUBLIC_URL_OVERRIDE before launching so the
    # GPU worker can call back (e.g. your stable trycloudflare / named tunnel URL).
    # Falls back to localhost for pure-local use.
    if ($env:PUBLIC_URL_OVERRIDE) { $env:PUBLIC_URL = $env:PUBLIC_URL_OVERRIDE }
    elseif (-not $env:PUBLIC_URL) { $env:PUBLIC_URL = "http://localhost:8000" }
    $env:FRONTEND_URL = "http://localhost:5173"
    $env:VIDYRA_VOICE_DIR = Join-Path $root "models\piper"
    $env:PATH = (Join-Path $root ".venv\Scripts") + ";" + (Join-Path $root "vidyra-tools\ffmpeg-9.0.1-essentials_build\bin") + ";" + $env:PATH
<<<<<<< HEAD
    $storageEnv = Join-Path $root "backend\config\storage.env"
    if (Test-Path $storageEnv) {
        Get-Content $storageEnv | ForEach-Object {
            $line = $_.Trim()
            if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
                $i = $line.IndexOf('=')
                Set-Item -Path ("env:" + $line.Substring(0, $i).Trim()) -Value $line.Substring($i + 1).Trim().Trim('"')
            }
        }
    }
    Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000" -WorkingDirectory $root -RedirectStandardError $backlog -WindowStyle Hidden
=======
    Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", "8000" -WorkingDirectory $root -RedirectStandardError $backlog -WindowStyle Hidden
>>>>>>> 6994d3f887877433e80f105f97671716065a31ae
}

$up = $false
try { Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 3 | Out-Null; $up = $true } catch { }

if (-not $up) {
    Write-Host "Starting backend..."
    Start-Backend
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 2
        try { Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 2 | Out-Null; $up = $true; break } catch { }
    }
    if (-not $up) { Write-Host "Backend failed to start. See $backlog"; exit 1 }
}
Write-Host "Backend running at http://127.0.0.1:8000"

Write-Host "Starting public tunnel..."
$proc = Start-Process -FilePath $cf -ArgumentList "tunnel", "--url", "http://127.0.0.1:8000", "--no-autoupdate" -RedirectStandardOutput $tunlog -RedirectStandardError $tunerr -WindowStyle Hidden -PassThru
$url = $null
for ($i = 0; $i -lt 40; $i++) {
    Start-Sleep -Seconds 2
    foreach ($f in @($tunlog, $tunerr)) {
        if (Test-Path $f) {
            $m = Select-String -Path $f -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -First 1
            if ($m) { $url = $m.Matches[0].Value; break }
        }
    }
    if ($url) { break }
}
if (-not $url) { Write-Host "Tunnel failed to start. See $tunerr / $tunlog"; exit 1 }

Write-Host ""
Write-Host "============================================================"
Write-Host "  PUBLIC URL - open this from any device, anywhere:"
Write-Host "  $url"
Write-Host "============================================================"
Write-Host "Keep this window open while you use the app."
Write-Host "Press Ctrl+C to stop public access for today."

$proc.WaitForExit()