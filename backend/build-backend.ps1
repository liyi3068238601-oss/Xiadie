# 在 Windows 上把 FastAPI 后端冻结成独立可执行（PyInstaller）。
# 前置：已安装 Python 3.10–3.12（安装时勾选 "Add Python to PATH"）。
# 用法（PowerShell，在 backend\ 目录）：  .\build-backend.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "== 创建构建用虚拟环境 ==" -ForegroundColor Cyan
if (Test-Path ".venv-build") { Remove-Item -Recurse -Force ".venv-build" }
py -3 -m venv .venv-build
$py = ".\.venv-build\Scripts\python.exe"

Write-Host "== 安装依赖 + PyInstaller ==" -ForegroundColor Cyan
& $py -m pip install --upgrade pip | Out-Null
& $py -m pip install -r requirements.txt pyinstaller

Write-Host "== 冻结后端 ==" -ForegroundColor Cyan
if (Test-Path "build") { Remove-Item -Recurse -Force "build" }
if (Test-Path "dist")  { Remove-Item -Recurse -Force "dist" }
& $py -m PyInstaller xiadie-backend.spec --noconfirm --log-level WARN

$exe = "dist\xiadie-backend\xiadie-backend.exe"
if (Test-Path $exe) {
  Write-Host "✓ 后端已冻结: backend\$exe" -ForegroundColor Green
} else {
  Write-Error "冻结失败：未找到 $exe"
  exit 1
}
