@echo off
REM Run today's slate with validation
REM Delegates to run_today.ps1 for full validation

cd /d "%~dp0"

set "DATE_ARG="
set "VERBOSE_FLAG="

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--verbose" (
    set "VERBOSE_FLAG=-VerboseMode"
    shift
    goto parse_args
)
if not defined DATE_ARG (
    set "DATE_ARG=%~1"
)
shift
goto parse_args

:args_done
if "%DATE_ARG%"=="" set "DATE_ARG=%date:~0,4%-%date:~5,2%-%date:~8,2%"

powershell -ExecutionPolicy Bypass -File "%~dp0run_today.ps1" -Date "%DATE_ARG%" %VERBOSE_FLAG%
exit /b %ERRORLEVEL%
