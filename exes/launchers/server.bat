@echo off
REM Interactive server launcher - keeps terminal open for stdout/stdin
echo Starting Server...
echo Press Ctrl+C to stop the server
echo.

REM Run the server without redirecting output, allowing full interaction
"%~dp0..\dist\run_servers\run_servers.exe" "%~dp0config.json"

REM This will only execute if the server exits normally
echo.
echo Server has stopped with exit code %errorlevel%
echo Press any key to close this window...
pause > nul