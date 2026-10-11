@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

set "APP_PY=%CD%\.venv\Scripts\python.exe"

powershell.exe -NoProfile -Command "$ErrorActionPreference = 'Stop'; $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/api/markets' -TimeoutSec 2; if ($response.StatusCode -eq 200) { exit 0 }; exit 1" >nul 2>nul
if not errorlevel 1 goto already_running

where uv >nul 2>nul
if errorlevel 1 goto setup_python

echo [1/2] Preparing the application environment with uv...
uv sync
if errorlevel 1 goto failed
goto launch

:setup_python
if exist "%APP_PY%" goto install_packages

where py >nul 2>nul
if errorlevel 1 goto try_python
echo [1/2] Creating a Python virtual environment...
py -3 -m venv .venv
if errorlevel 1 goto failed
goto install_packages

:try_python
where python >nul 2>nul
if errorlevel 1 goto no_python
echo [1/2] Creating a Python virtual environment...
python -m venv .venv
if errorlevel 1 goto failed

:install_packages
echo [1/2] Installing required packages...
"%APP_PY%" -m pip install -e .
if errorlevel 1 goto failed

:launch
if not exist "%APP_PY%" goto failed
echo [2/2] Starting the COT dashboard...
echo.
echo Dashboard: http://127.0.0.1:8000
echo To stop, press Ctrl+C or close this window.
echo.
start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 3; Start-Process 'http://127.0.0.1:8000'"
"%APP_PY%" main.py
if errorlevel 1 goto failed
exit /b 0

:already_running
echo The COT dashboard is already running.
echo Opening http://127.0.0.1:8000 ...
start "" "http://127.0.0.1:8000"
exit /b 0

:no_python
echo.
echo Python 3.11 or newer is required.
echo Install Python from https://www.python.org/downloads/ and run this file again.
echo.
pause
exit /b 1

:failed
echo.
echo Setup or startup failed. Check the message above, then try again.
echo.
pause
exit /b 1
