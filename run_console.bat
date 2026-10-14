@echo off
rem Launch with a console window so errors are visible.
setlocal
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
)
fc /b requirements.txt .venv\requirements.installed >nul 2>nul
if errorlevel 1 (
  .venv\Scripts\python -m pip install -r requirements.txt && copy /y requirements.txt .venv\requirements.installed >nul
)
.venv\Scripts\python -m imiwatch %*
pause
