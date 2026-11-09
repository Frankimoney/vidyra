# Build + push the Vidyra AI Studio RunPod worker image and create a
# serverless endpoint. Run from the repo root in PowerShell:
#
#   powershell -ExecutionPolicy Bypass -File deploy\runpod\setup_runpod.ps1
#
# Prereqs: Docker Desktop running, a RunPod account + API key, and
# runpodctl installed (https://github.com/runpod/runpodctl/releases).
#
# After it finishes, copy the printed RUNPOD_API_KEY / RUNPOD_ENDPOINT_ID into
# backend\config\studio.env and restart with "Start Vidyra PC.bat".

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

$image   = if ($env:IMAGE)   { $env:IMAGE }   else { 'YOUR_DOCKERHUB_USER/vidyra-runpod-worker:latest' }
$gpu     = if ($env:GPU)     { $env:GPU }     else { 'NVIDIA RTX A5000' }
$workers = if ($env:MIN_WORKERS) { $env:MIN_WORKERS } else { '0' }
$name    = if ($env:ENDPOINT_NAME) { $env:ENDPOINT_NAME } else { 'vidyra-studio' }

Write-Output "==> Checking tools"
docker version --format '{{.Server.Version}}' | Out-Null
if (-not (Get-Command runpodctl -ErrorAction SilentlyContinue)) {
  throw "runpodctl not found. Install it from https://github.com/runpod/runpodctl/releases and re-run."
}

Write-Output "==> Building image: $image"
docker build -t $image -f "$root\deploy\runpod\Dockerfile" $root

Write-Output "==> Pushing image (docker login first if this fails)"
docker push $image

Write-Output "==> Creating serverless endpoint '$name'"
# runpodctl reads RUNPOD_API_KEY from the environment.
if (-not $env:RUNPOD_API_KEY) {
  throw "Set `$env:RUNPOD_API_KEY to your RunPod API key before running this script."
}

runpodctl serverless create `
  --name $name `
  --image $image `
  --gpu $gpu `
  --min-workers $workers `
  --max-workers 3 `
  --container-disk 40 `
  --network-volume-mount /runpod-volume

Write-Output ''
Write-Output '==> Done. Now put these in backend\config\studio.env :'
Write-Output "RUNPOD_API_KEY=$env:RUNPOD_API_KEY"
Write-Output "RUNPOD_ENDPOINT_ID=<endpoint id from the create output above>"
Write-Output ''
Write-Output 'Then restart with "Start Vidyra PC.bat".'
Write-Output 'Tip: to test the UI with no GPU cost, set RUNPOD_FAKE=1 instead.'