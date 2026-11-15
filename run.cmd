@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Run install.ps1 first.
  pause
  exit /b 1
)

wscript "%~dp0run.vbs"
