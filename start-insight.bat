@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" goto :novenv
".venv\Scripts\python.exe" launcher.py --only insight
echo.
pause
exit /b 0

:novenv
echo [ERROR] .venv not found in this folder. Please see SETUP.md.
pause
exit /b 1
