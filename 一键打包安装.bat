@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo    遐蝶 · 一键打包安装（Windows）
echo    首次运行需几分钟下载依赖，请耐心等待…
echo ============================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\one-click-build.ps1"
echo.
echo 按任意键关闭本窗口。
pause >nul
