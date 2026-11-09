@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "deploy\pc\start-vidyra-pc.ps1"
pause