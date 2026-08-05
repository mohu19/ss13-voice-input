@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Run 安装依赖.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -u setup_api.py
pause
