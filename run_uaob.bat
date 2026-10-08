@echo off
rem Sets up (first run) and starts UAOB on Windows. See scripts\setup_and_run.ps1.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup_and_run.ps1" %*
