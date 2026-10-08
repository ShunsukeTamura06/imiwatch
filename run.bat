@echo off
setlocal
cd /d "%~dp0"
if not exist .venv (
  echo First-time setup: creating .venv and installing packages...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
)
start "" .venv\Scripts\pythonw -m imiwatch
