@echo off
set "ROOT=%~dp0"
"%SystemRoot%\System32\wscript.exe" "%ROOT%scripts\start-hidden.vbs"
exit /b 0
