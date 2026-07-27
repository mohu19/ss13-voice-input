@echo off
cd /d "%~dp0"
echo ============================================
echo  SS13 Voice Input - Install Dependencies
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found! Install Python 3.8+ first.
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [OK] Python found
python --version

echo.
echo Installing libraries (using Tsinghua mirror for speed)...
python -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple/ --trusted-host pypi.tuna.tsinghua.edu.cn >nul 2>&1
python -m pip install sounddevice numpy pynput pywin32 mouse -i https://pypi.tuna.tsinghua.edu.cn/simple/ --trusted-host pypi.tuna.tsinghua.edu.cn
if errorlevel 1 (
    echo [WARN] Tsinghua mirror failed, trying default PyPI...
    python -m pip install sounddevice numpy pynput pywin32 mouse
)
if errorlevel 1 (
    echo [ERROR] Install failed. Try: pip install sounddevice numpy pynput pywin32 mouse
    pause
    exit /b 1
)

echo.
echo [Optional] Installing Vosk offline speech recognition...
python -m pip install vosk -i https://pypi.tuna.tsinghua.edu.cn/simple/ --trusted-host pypi.tuna.tsinghua.edu.cn 2>nul
if errorlevel 1 (
    echo [INFO] Vosk skipped - not required for main functionality
) else (
    echo [OK] Vosk installed
)

echo.
echo ============================================
echo  All done!
echo  Double-click [start.bat] to use.
echo ============================================
pause
