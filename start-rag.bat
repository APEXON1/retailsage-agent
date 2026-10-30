@echo off
cd /d "%~dp0"
set "PY=py -3"
if exist "C:\anaconda\python.exe" set "PY=C:\anaconda\python.exe"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
%PY% launcher.py --only kb
echo.
pause
exit /b 0
