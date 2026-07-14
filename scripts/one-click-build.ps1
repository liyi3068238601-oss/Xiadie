# 遐蝶 · 一键打包安装（由 一键打包安装.bat 调用）
# 自动：检查/安装 Node + Python → 构建前端 → 冻结后端 → 打成安装器 → 启动安装向导。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot   # 仓库根目录（本脚本在 scripts\ 下）

function Have($cmd) { $null -ne (Get-Command $cmd -ErrorAction SilentlyContinue) }
function RefreshPath() {
  $m = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
  $u = [System.Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = "$m;$u"
}
function Step($t) { Write-Host "`n===== $t =====" -ForegroundColor Cyan }

# ---------- 前置：Node ----------
Step "检查 Node.js"
if (-not (Have node)) {
  if (Have winget) {
    Write-Host "未检测到 Node.js，正在用 winget 自动安装…" -ForegroundColor Yellow
    winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
    RefreshPath
  }
}
if (-not (Have node)) {
  Write-Host "✗ 仍未检测到 Node.js。请手动安装后重试：https://nodejs.org/（选 LTS）" -ForegroundColor Red
  Write-Host "  装完请重新双击『一键打包安装.bat』。" -ForegroundColor Red
  exit 1
}
Write-Host "✓ Node $(node -v)" -ForegroundColor Green

# ---------- 前置：Python ----------
Step "检查 Python"
if (-not (Have python) -and -not (Have py)) {
  if (Have winget) {
    Write-Host "未检测到 Python，正在用 winget 自动安装 3.12…" -ForegroundColor Yellow
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    RefreshPath
  }
}
if (-not (Have python) -and -not (Have py)) {
  Write-Host "✗ 仍未检测到 Python。请手动安装 3.10–3.12（勾选 Add to PATH）：https://www.python.org/downloads/" -ForegroundColor Red
  Write-Host "  装完请重新双击『一键打包安装.bat』。" -ForegroundColor Red
  exit 1
}
Write-Host "✓ Python 已就绪" -ForegroundColor Green

# ---------- 构建 ----------
Step "开始构建（前端 → 后端 → 安装器）"
& "$root\build-windows.ps1"

# ---------- 启动安装向导 ----------
Step "打开安装向导"
$installer = Get-ChildItem "$root\dist-installer" -Filter *.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if ($installer) {
  Write-Host "✓ 安装器已生成：$($installer.FullName)" -ForegroundColor Green
  Write-Host "  正在打开安装向导，按提示点『下一步』即可完成安装。" -ForegroundColor Green
  Start-Process $installer.FullName
} else {
  Write-Host "✗ 未找到安装器，请查看上方构建日志排查。" -ForegroundColor Red
  exit 1
}
