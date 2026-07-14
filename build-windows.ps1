# 遐蝶 · Windows 一键打包
# 依次：构建前端 → 冻结后端 → electron-builder 打成 NSIS 安装器。
# 前置：Node ≥ 18、Python 3.10–3.12（都勾选 Add to PATH）。
# 用法（PowerShell，在仓库根目录）：  .\build-windows.ps1
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host "`n========== 1/3  构建前端 ==========" -ForegroundColor Cyan
Set-Location "$root\frontend"
npm install
npm run build

Write-Host "`n========== 2/3  冻结后端 ==========" -ForegroundColor Cyan
Set-Location "$root\backend"
& "$root\backend\build-backend.ps1"

Write-Host "`n========== 3/3  打包安装器 ==========" -ForegroundColor Cyan
Set-Location "$root\desktop"
npm install
npm run dist

Set-Location $root
Write-Host "`n✓ 完成！安装器在  dist-installer\" -ForegroundColor Green
Get-ChildItem "$root\dist-installer" -Filter *.exe -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "  → $($_.Name)" }
