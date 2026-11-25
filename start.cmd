@echo off
rem Arena Monitor.
rem   start.cmd      start the server in this window and open the dashboard
rem   start.cmd bg   start the server hidden (used by the logon task "arena-monitor"); no browser
cd /d "%~dp0"
if /i "%~1"=="bg" (
  start "" /b pythonw server.py
  exit /b
)
start "" http://localhost:8090
python server.py
