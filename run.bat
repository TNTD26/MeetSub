@echo off
setlocal
cd /d "%~dp0"
if exist "MeetSub.exe" (
    start "" "MeetSub.exe"
    exit /b 0
)
if exist "dist\MeetSub\MeetSub.exe" (
    start "" "dist\MeetSub\MeetSub.exe"
    exit /b 0
)
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m meetsub
    exit /b 0
)
echo MeetSub is not installed yet.
echo Run: powershell -ExecutionPolicy Bypass -File .\setup.ps1
pause
