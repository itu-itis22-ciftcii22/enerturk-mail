@echo off
REM
echo Starting Client SMTP...
echo Press Ctrl+C to stop
echo.

REM
"%~dp0..\dist\smtp_client\smtp_client.exe" "%~dp0config.json"

REM
echo.
echo Client has stopped with exit code %errorlevel%
echo Press any key to close this window...
pause > nul