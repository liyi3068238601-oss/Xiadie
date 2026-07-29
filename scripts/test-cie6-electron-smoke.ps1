param([int]$DurationSeconds = 8)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$tempRoot = Join-Path $projectRoot (".cie6-smoke-" + [Guid]::NewGuid().ToString("N"))
$backendPython = Join-Path $projectRoot "backend\.venv\Scripts\python.exe"
$electronExe = Join-Path $projectRoot "desktop\node_modules\electron\dist\electron.exe"
$devFlag = Join-Path $projectRoot "backend\.dev_mode"
$createdDevFlag = -not (Test-Path -LiteralPath $devFlag)
$started = @()

function Test-Port([int]$Port) {
  return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Stop-ProcessTree([System.Diagnostics.Process]$Process) {
  if (-not $Process) { return }
  $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($Process.Id)" -ErrorAction SilentlyContinue
  foreach ($child in $children) {
    try { Stop-ProcessTree ([System.Diagnostics.Process]::GetProcessById($child.ProcessId)) } catch {}
  }
  try { Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue } catch {}
}

try {
  if ((Test-Port 8756) -or (Test-Port 5173)) {
    throw "CIE.6 smoke requires free ports 8756 and 5173; existing processes were not touched."
  }
  if (-not (Test-Path -LiteralPath $backendPython)) { throw "backend venv missing" }
  if (-not (Test-Path -LiteralPath $electronExe)) { throw "Electron runtime missing" }
  New-Item -ItemType Directory -Path $tempRoot | Out-Null
  New-Item -ItemType Directory -Path (Join-Path $tempRoot "data") | Out-Null
  if ($createdDevFlag) { [System.IO.File]::WriteAllText($devFlag, "1") }

  $env:XIADIE_API_TOKEN = [Guid]::NewGuid().ToString("N") + [Guid]::NewGuid().ToString("N")
  $env:XIADIE_DATA_DIR = Join-Path $tempRoot "data"
  $env:XIADIE_DEV_MODE = "1"
  $env:XIADIE_PARENT_PID = [string]$PID

  $backend = Start-Process -FilePath $backendPython -ArgumentList "run_frozen.py" `
    -WorkingDirectory (Join-Path $projectRoot "backend") -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $tempRoot "backend.out.log") `
    -RedirectStandardError (Join-Path $tempRoot "backend.err.log")
  $started += $backend
  $npm = (Get-Command npm.cmd -ErrorAction Stop).Source
  $frontend = Start-Process -FilePath $npm -ArgumentList "run", "dev" `
    -WorkingDirectory (Join-Path $projectRoot "frontend") -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput (Join-Path $tempRoot "frontend.out.log") `
    -RedirectStandardError (Join-Path $tempRoot "frontend.err.log")
  $started += $frontend

  $backendReady = $false
  $frontendReady = $false
  for ($attempt = 0; $attempt -lt 60; $attempt++) {
    try {
      $health = Invoke-RestMethod "http://127.0.0.1:8756/api/health" -TimeoutSec 1
      $backendReady = $health.status -eq "ok"
    } catch {}
    try {
      $page = Invoke-WebRequest "http://127.0.0.1:5173/" -UseBasicParsing -TimeoutSec 1
      $frontendReady = $page.StatusCode -eq 200
    } catch {}
    if ($backendReady -and $frontendReady) { break }
    Start-Sleep -Milliseconds 500
  }
  if (-not $backendReady -or -not $frontendReady) { throw "local services did not become ready" }

  $electron = Start-Process -FilePath $electronExe -ArgumentList "." `
    -WorkingDirectory (Join-Path $projectRoot "desktop") -WindowStyle Hidden -PassThru
  $started += $electron
  Start-Sleep -Seconds ([Math]::Max(3, $DurationSeconds))
  $electron.Refresh()
  if ($electron.HasExited) { throw "Electron exited before the smoke window completed" }
  if (-not (Test-Port 8756) -or -not (Test-Port 5173)) { throw "a local service exited during Electron smoke" }

  [ordered]@{
    protocol_version = "cie-electron-smoke-v1"
    status = "pass"
    platform = [Environment]::OSVersion.VersionString
    backend_health = $backendReady
    frontend_load = $frontendReady
    electron_alive_seconds = [Math]::Max(3, $DurationSeconds)
    isolated_data_dir = $true
    ports_owned_by_smoke = @(8756, 5173)
  } | ConvertTo-Json -Depth 4
}
finally {
  [array]::Reverse($started)
  foreach ($process in $started) { Stop-ProcessTree $process }
  foreach ($port in 8756, 5173) {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($listener) {
      $owned = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
      if ($owned -and $owned.CommandLine -like "*$projectRoot*") {
        Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
      }
    }
  }
  if ($createdDevFlag) { Remove-Item -LiteralPath $devFlag -Force -ErrorAction SilentlyContinue }
  $resolvedTemp = [System.IO.Path]::GetFullPath($tempRoot)
  if ($resolvedTemp.StartsWith($projectRoot + [System.IO.Path]::DirectorySeparatorChar)) {
    Remove-Item -LiteralPath $resolvedTemp -Recurse -Force -ErrorAction SilentlyContinue
  }
}
