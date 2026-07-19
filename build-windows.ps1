# Xiadie Windows one-command build.
# Builds the frontend, freezes the backend, then creates the NSIS installer.
# Requirements: Node >= 18 and Python 3.10-3.12 available on PATH.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host "`n========== 1/4  Build frontend ==========" -ForegroundColor Cyan
Set-Location "$root\frontend"
npm install
npm run build

Write-Host "`n========== 2/4  Freeze backend ==========" -ForegroundColor Cyan
Set-Location "$root\backend"
& "$root\backend\build-backend.ps1"

Write-Host "`n========== 3/4  Build installer ==========" -ForegroundColor Cyan
Set-Location "$root\desktop"
npm install
$modelSource = Join-Path (Split-Path $root -Parent) "bge-m3"
$modelStage = Join-Path $root "desktop\model-stage\bge-m3"
New-Item -ItemType Directory -Force $modelStage | Out-Null
if (Test-Path (Join-Path $modelSource "onnx\model_quantized.onnx")) {
  Write-Host "== Stage local BGE-M3 (adds about 543 MiB to installer) ==" -ForegroundColor Cyan
  Copy-Item -Path (Join-Path $modelSource "*") -Destination $modelStage -Recurse -Force
} else {
  Write-Warning "Missing ..\bge-m3; the installer will keep FTS but omit the local vector model."
}
npm run dist

Write-Host "`n========== 4/4  Verify release resources ==========" -ForegroundColor Cyan
& "$root\scripts\verify-release-resources.ps1"

Set-Location $root
Write-Host "`nBuild complete. Installers are in dist-installer\" -ForegroundColor Green
Get-ChildItem "$root\dist-installer" -Filter *.exe -ErrorAction SilentlyContinue |
  ForEach-Object { Write-Host "  -> $($_.Name)" }
