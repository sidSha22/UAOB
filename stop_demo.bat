@echo off
rem Stops the demo (app, API, local model, Neo4j container). The graph data is kept.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\stop_demo.ps1"
pause
