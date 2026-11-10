# Starts the PC backend (+ dev server). Double-click "Start Vidyra PC.bat".
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

function Stop-Port($port) {
  try {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) { Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue }
  } catch {}
}

Write-Output 'Stopping anything already on ports 8000/5173...'
Stop-Port 8000
Stop-Port 5173

$env:FIREBASE_PROJECT_ID = 'vidyra'
$env:SERVICE_ACCOUNT_PATH = "$root\backend\config\vidyra-firebase-adminsdk-fbsvc-31c93a2e64.json"
$env:GOOGLE_APPLICATION_CREDENTIALS = $env:SERVICE_ACCOUNT_PATH
$env:APP_ENV = 'development'
$env:DEV_API_KEY = 'vidyra-dev-key'
$env:CORS_ORIGINS = 'http://localhost:5173,http://localhost:4173,http://192.168.43.176:5173,https://vidyra.web.app'
# R2_ACCOUNT_ID / R2_BUCKET / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY are required.
$env:STORAGE_BACKEND = 'r2'
$env:STORAGE_ROOT = "$root\storage\cache"
$env:WORK_ROOT = "$root\storage\work"
$env:PUBLIC_URL = 'http://192.168.43.176:8000'
$env:FRONTEND_URL = 'http://localhost:5173'
$env:VIDYRA_VOICE_DIR = "$root\models\piper"
$env:VIDYRA_MUSIC_DIR = "$root\config\music"
$env:VIDYRA_FRONTEND_DIR = "$root\frontend\dist"
$env:PATH = "$root\.venv\Scripts;$root\vidyra-tools\ffmpeg-9.0.1-essentials_build\bin;$env:PATH"

# AI Studio (RunPod) config: KEY=VALUE lines in backend\config\studio.env.
# Put RUNPOD_API_KEY / RUNPOD_ENDPOINT_ID there, or RUNPOD_FAKE=1 for local
# placeholder runs. Missing file => AI Studio stays disabled (graceful).
$studioEnv = "$root\backend\config\studio.env"
if (Test-Path $studioEnv) {
  Write-Output "Loading AI Studio config from $studioEnv"
  Get-Content $studioEnv | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
      $i = $line.IndexOf('=')
      $k = $line.Substring(0, $i).Trim()
      $v = $line.Substring($i + 1).Trim().Trim('"')
      Set-Item -Path "env:$k" -Value $v
    }
  }
}

Write-Output 'Starting backend (http://<this-pc-ip>:8000)...'
Start-Process -WorkingDirectory $root `
  -FilePath "$root\.venv\Scripts\python.exe" `
  -ArgumentList '-m','uvicorn','app.main:app','--app-dir','backend','--host','0.0.0.0','--port','8000' `
  -RedirectStandardError "$temp\uvicorn-err.log" `
  -RedirectStandardOutput "$temp\uvicorn-out.log" `
  -WindowStyle Hidden

Write-Output 'Starting dev server (http://localhost:5173)...'
Start-Process -WorkingDirectory "$root\frontend" `
  -FilePath 'cmd.exe' `
  -ArgumentList '/c','npm','run','dev' `
  -RedirectStandardError "$temp\vite-err.log" `
  -RedirectStandardOutput "$temp\vite-out.log" `
  -WindowStyle Hidden

Write-Output ''
Write-Output 'Backend + dev server launching. Waiting for health check...'
$ok = $false
for ($i = 0; $i -lt 25; $i++) {
  Start-Sleep -Seconds 1
  $code = & curl.exe -s -o NUL -w '%{http_code}' --max-time 2 http://localhost:8000/api/health 2>$null
  if ($code -eq '200') { $ok = $true; break }
}
if ($ok) {
  $body = & curl.exe -s --max-time 3 http://localhost:8000/api/health 2>$null
  Write-Output ("OK. Backend health: " + $body)
  Write-Output ''
  Write-Output 'Open the app:'
  Write-Output '  On this PC :  http://localhost:8000   (or http://localhost:5173 for dev)'
  Write-Output '  On your phone/hotspot :  http://192.168.43.176:8000'
  Write-Output ''
  Write-Output 'Phone cannot connect? Run these in an ADMIN PowerShell once:'
  Write-Output '  netsh advfirewall firewall add rule name="Vidyra Backend" dir=in action=allow protocol=TCP localport=8000'
  Write-Output '  netsh advfirewall firewall add rule name="Vidyra Dev"     dir=in action=allow protocol=TCP localport=5173'
} else {
  Write-Output 'Backend did not answer health check. See:'
  Write-Output "  $temp\uvicorn-err.log"
}