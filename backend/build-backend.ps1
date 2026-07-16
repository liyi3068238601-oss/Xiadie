# Freeze the FastAPI backend into a standalone Windows executable.
# Requirements: Python 3.10-3.12 available on PATH.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Resolve-PythonCommand {
  if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 -c "import sys; assert sys.version_info >= (3, 10)" 2>$null
    if ($LASTEXITCODE -eq 0) { return @{ Command = "py"; Args = @("-3") } }
  }
  if (Get-Command python -ErrorAction SilentlyContinue) {
    & python -c "import sys; assert sys.version_info >= (3, 10)" 2>$null
    if ($LASTEXITCODE -eq 0) { return @{ Command = "python"; Args = @() } }
  }
  throw "Python 3.10+ was not found. Install Python 3.12 and try again."
}

Write-Host "== Create build virtual environment ==" -ForegroundColor Cyan
if (Test-Path ".venv-build") { Remove-Item -Recurse -Force ".venv-build" }
$python = Resolve-PythonCommand
& $python.Command @($python.Args) -m venv .venv-build
$py = ".\.venv-build\Scripts\python.exe"

Write-Host "== Install dependencies and PyInstaller ==" -ForegroundColor Cyan
& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install -r requirements.txt pyinstaller

Write-Host "== Freeze backend ==" -ForegroundColor Cyan
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist")  { Remove-Item -Recurse -Force "dist" }
& $py -m PyInstaller xiadie-backend.spec --noconfirm --log-level WARN

$exe = "dist\xiadie-backend\xiadie-backend.exe"
if (Test-Path $exe) {
  Write-Host "Backend frozen: backend\$exe" -ForegroundColor Green
} else {
  Write-Error "Freeze failed: missing $exe"
  exit 1
}
