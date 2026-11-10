@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "scripts\setup_storage_env.py"
echo.
pause
