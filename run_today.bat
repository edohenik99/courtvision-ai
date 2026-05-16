@echo off
REM Run today's slate with validation
REM Delegates to run_today.ps1 for full validation
REM run_today.ps1 writes the completion_state_audit artifacts after summaries

cd /d "%~dp0"

set "DATE_ARG="
set "VERBOSE_FLAG="
set "FORCE_DATE_FLAG="

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--verbose" (
    set "VERBOSE_FLAG=-VerboseMode"
    shift
    goto parse_args
)
if /I "%~1"=="--force-past-date" (
    set "FORCE_DATE_FLAG=-ForcePastDate"
    shift
    goto parse_args
)
if not defined DATE_ARG (
    set "DATE_ARG=%~1"
)
shift
goto parse_args

:args_done
if "%DATE_ARG%"=="" for /f "delims=" %%D in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd"') do set "DATE_ARG=%%D"

powershell -ExecutionPolicy Bypass -File "%~dp0run_today.ps1" -Date "%DATE_ARG%" %VERBOSE_FLAG% %FORCE_DATE_FLAG%
exit /b %ERRORLEVEL%
