@echo off
setlocal
cd /d "%~dp0"
if not exist .venv (
  echo First-time setup: creating .venv ...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
)
rem Reinstall packages when requirements.txt has changed.
fc /b requirements.txt .venv\requirements.installed >nul 2>nul
if errorlevel 1 (
  echo Installing packages ...
  .venv\Scripts\python -m pip install -r requirements.txt && copy /y requirements.txt .venv\requirements.installed >nul
)
start "" .venv\Scripts\pythonw -m imiwatch
