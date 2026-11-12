@echo off
setlocal
cd /d "%~dp0..\..\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-LZ11.ps1" -InstallAutoStart
if errorlevel 1 (
  echo ERROR: demo was not started. Review Docker Desktop and the PowerShell output.
  pause
  exit /b 1
)
echo Demo started. Finish one-time Kuma administrator and employee/admin users.
pause
