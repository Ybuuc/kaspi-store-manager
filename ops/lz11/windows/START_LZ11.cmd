@echo off
setlocal EnableExtensions
title Kaspi Store Manager LZ11 - Diagnostics
echo ================================================================
echo Kaspi Store Manager: starting Docker test environment
echo ================================================================
if not exist "%~dp0Start-LZ11.ps1" (
 echo ERROR: Start-LZ11.ps1 is missing next to this file.
 goto :fail
)
if not exist "%~dp0Common.ps1" (
 echo ERROR: Common.ps1 is missing next to this file.
 goto :fail
)
cd /d "%~dp0..\..\.."
if errorlevel 1 goto :fail
if not exist "ops\lz11\compose.yaml" (
 echo ERROR: compose.yaml missing. Download/clone the FULL repo.
 goto :fail
)
echo Repo path: %CD%
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-LZ11.ps1" -InstallAutoStart
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
 echo ERROR: Startup failed with exit code %RESULT%
 echo Diagnostics: %LOCALAPPDATA%\KaspiStoreManagerLZ11\start-last.log
 goto :fail
)
echo SUCCESS: open http://127.0.0.1:5000/login
pause
exit /b 0
:fail
echo.
echo Press any key after reading the error above.
pause
exit /b 1
