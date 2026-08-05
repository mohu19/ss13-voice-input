@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Run install-deps.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -u setup_api.py
pause
