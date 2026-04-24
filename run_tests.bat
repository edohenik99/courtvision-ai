@echo off
echo Running stable tests...
python -m pytest tests/stable/ -q
echo.
echo Tests completed with exit code %ERRORLEVEL%
pause
