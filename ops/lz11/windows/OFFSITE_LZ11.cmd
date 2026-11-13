@echo off
setlocal
cd /d "%~dp0..\..\.."
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Export-Backups.ps1"
if errorlevel 1 (
  echo ERROR: Google Drive copy or SHA256 restore check failed. No scheduled task was registered.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-OffsiteTask.ps1"
if errorlevel 1 (
  echo ERROR: scheduled task registration failed or task already exists.
  pause
  exit /b 1
)
echo Offsite copy verified and scheduled for every 15 minutes while signed in.
pause
