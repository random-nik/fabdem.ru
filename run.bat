@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 exit /b 1
)

echo Installing dependencies...
.venv\Scripts\python.exe -m pip install -e . --quiet
if errorlevel 1 exit /b 1

echo.
echo FABDEM downloader: http://127.0.0.1:8000
echo Press Ctrl+C to stop.
echo.
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
