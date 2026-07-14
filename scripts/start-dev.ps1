$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$backendPython = Join-Path $root "backend\.venv\Scripts\python.exe"
$frontendDir = Join-Path $root "frontend"
$desktopDir = Join-Path $root "desktop"
$electronExe = Join-Path $desktopDir "node_modules\electron\dist\electron.exe"
$logRoot = if ($env:LOCALAPPDATA) {
  Join-Path $env:LOCALAPPDATA "Xiadie\dev-logs"
} else {
  Join-Path $env:TEMP "Xiadie\dev-logs"
}

New-Item -ItemType Directory -Force -Path $logRoot | Out-Null

function Show-LaunchError([string]$message) {
  try {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
      $message,
      "Xiadie startup failed",
      [System.Windows.MessageBoxButton]::OK,
      [System.Windows.MessageBoxImage]::Error
    ) | Out-Null
  } catch {
    Write-Error $message
  }
}

function Test-Port([int]$port) {
  try {
    return $null -ne (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop)
  } catch {
    return $false
  }
}

function Stop-ProcessTree([System.Diagnostics.Process]$process) {
  if (-not $process -or $process.HasExited) { return }
  & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
}

$startedBackend = $null
$startedFrontend = $null

try {
  if (-not (Test-Path -LiteralPath $backendPython)) {
    throw "Backend virtual environment is missing. Expected:`n$backendPython"
  }
  if (-not (Test-Path -LiteralPath $electronExe)) {
    throw "Electron runtime is incomplete. Run npm ci in the desktop directory."
  }

  $existingElectron = Get-CimInstance Win32_Process -Filter "Name = 'electron.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -eq $electronExe } |
    Select-Object -First 1
  if ($existingElectron) {
    exit 0
  }

  if (-not (Test-Port 8756)) {
    $startedBackend = Start-Process `
      -FilePath $backendPython `
      -ArgumentList "run_frozen.py" `
      -WorkingDirectory (Join-Path $root "backend") `
      -RedirectStandardOutput (Join-Path $logRoot "backend.out.log") `
      -RedirectStandardError (Join-Path $logRoot "backend.err.log") `
      -WindowStyle Hidden `
      -PassThru
  }

  if (-not (Test-Port 5173)) {
    $npm = (Get-Command npm.cmd -ErrorAction Stop).Source
    $startedFrontend = Start-Process `
      -FilePath $npm `
      -ArgumentList "run", "dev" `
      -WorkingDirectory $frontendDir `
      -RedirectStandardOutput (Join-Path $logRoot "frontend.out.log") `
      -RedirectStandardError (Join-Path $logRoot "frontend.err.log") `
      -WindowStyle Hidden `
      -PassThru
  }

  $backendReady = $false
  $frontendReady = $false
  for ($i = 0; $i -lt 40; $i++) {
    if (-not $backendReady) {
      try {
        $health = Invoke-RestMethod "http://127.0.0.1:8756/api/health" -TimeoutSec 1
        $backendReady = $health.status -eq "ok"
      } catch {}
    }
    if (-not $frontendReady) {
      try {
        $page = Invoke-WebRequest "http://127.0.0.1:5173/" -UseBasicParsing -TimeoutSec 1
        $frontendReady = $page.StatusCode -eq 200
      } catch {}
    }
    if ($backendReady -and $frontendReady) { break }
    Start-Sleep -Milliseconds 500
  }

  if (-not $backendReady -or -not $frontendReady) {
    throw "Local services did not start. Logs:`n$logRoot"
  }

  $desktop = Start-Process `
    -FilePath $electronExe `
    -ArgumentList "." `
    -WorkingDirectory $desktopDir `
    -PassThru

  Wait-Process -Id $desktop.Id
} catch {
  Show-LaunchError $_.Exception.Message
} finally {
  Stop-ProcessTree $startedFrontend
  Stop-ProcessTree $startedBackend
}
