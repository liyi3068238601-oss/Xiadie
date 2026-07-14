@echo off
set "ROOT=%~dp0"
start "" "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%ROOT%scripts\start-dev.ps1"
exit /b 0
