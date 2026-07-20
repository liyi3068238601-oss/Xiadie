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
Remove-Item -LiteralPath (Join-Path $logRoot "launcher.err.log") -Force -ErrorAction SilentlyContinue

function Show-LaunchError([string]$message) {
  # 写错误文件到桌面，确保用户能看到（hidden 窗口下 MessageBox 可能不显示）
  try {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $errFile = Join-Path $desktop "遐蝶启动失败.txt"
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $content = "遐蝶启动失败`r`n时间: $stamp`r`n错误: $message`r`n`r`n请检查是否已有遐蝶实例在运行，或查看日志：`r`n$([Environment]::GetFolderPath('LocalApplicationData'))\Xiadie\dev-logs\"
    [System.IO.File]::WriteAllText($errFile, $content, [System.Text.UTF8Encoding]::new($true))
  } catch {}
  try {
    Add-Type -AssemblyName PresentationFramework
    [System.Windows.MessageBox]::Show(
      $message,
      "遐蝶启动失败",
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
  # 全局 $ErrorActionPreference = "Stop" 会导致 taskkill 在进程已退出时的
  # stderr 输出变成终止错误，脚本提前退出并触发 finally 杀掉所有子进程。
  # 临时切到 Continue 模式，吞掉 taskkill 的所有错误。
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    & taskkill.exe /PID $process.Id /T /F 2>&1 | Out-Null
  } finally {
    $ErrorActionPreference = $prev
  }
}

function Get-ListenerProcess([int]$port) {
  try {
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop |
      Select-Object -First 1
    if ($listener) {
      return Get-Process -Id $listener.OwningProcess -ErrorAction Stop
    }
  } catch {}
  return $null
}

$startedBackend = $null
$startedFrontend = $null
$backendListener = $null
$frontendListener = $null

try {
  if (-not (Test-Path -LiteralPath $backendPython)) {
    throw "Backend virtual environment is missing. Expected:`n$backendPython"
  }
  if (-not (Test-Path -LiteralPath $electronExe)) {
    throw "Electron runtime is incomplete. Run npm ci in the desktop directory."
  }

  # 后端先于 Electron 启动，开发启动器为两个子进程生成同一枚临时令牌。
  # 令牌只存在于当前进程树的环境中，不写入日志、URL 或磁盘。
  # 两个独立 UUID 提供约 244 bit 随机强度；纯 .NET 写法不依赖 PATH 或外部命令。
  $tokenPart1 = [Guid]::NewGuid().ToString("N")
  $tokenPart2 = [Guid]::NewGuid().ToString("N")
  $env:XIADIE_API_TOKEN = [string]::Concat($tokenPart1, $tokenPart2)
  $env:XIADIE_PARENT_PID = [string]$PID
  # dev 模式标记：security.py 对 vite origin 放行无 token 请求，
  # main.js 也以此作为 isDev 的可靠补充判断。
  $env:XIADIE_DEV_MODE = "1"

  $existingElectron = Get-CimInstance Win32_Process -Filter "Name = 'electron.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -eq $electronExe } |
    Select-Object -First 1
  if ($existingElectron) {
    exit 0
  }

  if (Test-Port 8756) {
    throw "Backend port 8756 is already in use. Exit the existing backend and try again."
  } else {
    # dev 模式文件标志：venv launcher 派生子进程时可能丢失 XIADIE_DEV_MODE
    # 环境变量，文件标志不受进程派生影响，security.py 优先检查它。
    $devFlag = Join-Path $root "backend\.dev_mode"
    Set-Content -LiteralPath $devFlag -Value "1" -Encoding UTF8 -NoNewline

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

  # npm.cmd 和虚拟环境 Python 都可能再派生真正监听端口的子进程。
  # 单独保留监听进程对象，退出时与外层启动进程一起清理。
  $backendListener = Get-ListenerProcess 8756
  $frontendListener = Get-ListenerProcess 5173

  $desktop = Start-Process `
    -FilePath $electronExe `
    -ArgumentList "." `
    -WorkingDirectory $desktopDir `
    -RedirectStandardOutput (Join-Path $logRoot "desktop.out.log") `
    -RedirectStandardError (Join-Path $logRoot "desktop.err.log") `
    -PassThru

  Wait-Process -Id $desktop.Id
} catch {
  $launcherError = @(
    "[$(Get-Date -Format o)] $($_.Exception.Message)"
    $_.ScriptStackTrace
  ) -join [Environment]::NewLine
  Add-Content -LiteralPath (Join-Path $logRoot "launcher.err.log") -Value $launcherError -Encoding UTF8
  Show-LaunchError $_.Exception.Message
} finally {
  # 先尝试杀之前保存的进程对象（进程树方式）
  Stop-ProcessTree $frontendListener
  Stop-ProcessTree $backendListener
  Stop-ProcessTree $startedFrontend
  Stop-ProcessTree $startedBackend
  Stop-ProcessTree $desktop

  # 兜底：按端口和进程名清理所有残留进程。
  # venv launcher 派生的 codex-runtimes python 子进程可能成为孤儿，
  # 之前的进程对象杀不到它；直接按端口定位并杀掉监听者更可靠。
  foreach ($port in 8756, 5173) {
    try {
      $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop |
        Select-Object -First 1
      if ($listener) {
        Stop-ProcessTree (Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue)
      }
    } catch {}
  }
  # 清理所有本项目 Electron 进程（防止 GPU/utility/renderer 子进程残留）
  Get-Process -Name electron -ErrorAction SilentlyContinue |
    Where-Object { try { $_.Path -like "$desktopDir*" } catch { $false } } |
    Stop-Process -Force -ErrorAction SilentlyContinue

  # 清理 dev 模式文件标志
  $devFlag = Join-Path $root "backend\.dev_mode"
  Remove-Item -LiteralPath $devFlag -Force -ErrorAction SilentlyContinue
}
