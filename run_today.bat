@echo off
REM Run today's slate with validation
REM Delegates to run_today.ps1 for full validation

cd /d "%~dp0"

set DATE=%1
if "%DATE%"=="" set DATE=%date:~0,4%-%date:~5,2%-%date:~8,2%

powershell -ExecutionPolicy Bypass -File "%~dp0run_today.ps1" -Date %DATE%
exit /b %ERRORLEVEL%
