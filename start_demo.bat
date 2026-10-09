@echo off
rem Starts everything for a live demo (Neo4j, local model, API, app) and opens the browser.
rem Open Docker Desktop first. See DEMO.md.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_demo.ps1" %*
