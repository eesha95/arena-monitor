@echo off
rem Cockpit launcher for the Arena Monitor phasedoc (port 6793).
rem Shared cockpit code lives in %USERPROFILE%\phasedoc-tools\cockpit.py - never copy it.
set "VAULT=%USERPROFILE%\OneDrive\Desktop\Obsidian\FDE projects"
set "PHASEDOC_DB=%~dp0plan.db"
set "PHASEDOC_PLAN_DOC=%VAULT%\eesha\phasedocs\arena-monitor\PHASEDOC.md"
set "PHASEDOC_ARCH_DOC=%VAULT%\eesha\phasedocs\arena-monitor\ARCHITECTURE.md"
set "PHASEDOC_PROGRESS_LOG=%VAULT%\eesha\phasedocs\arena-monitor\PROGRESS.md"
set "PHASEDOC_PROGRESS_PATTERN=\[arena\]"
set "PHASEDOC_RESOURCES=%~dp0resources.json"
set "PHASEDOC_EXPLAINERS=%~dp0explainers.json"
set "PHASEDOC_TITLE=Arena Monitor"
set "PHASEDOC_SUBTITLE=Phasedoc opened 2026-11-23 - real-time esports and gaming monitor (P0-P5), seeded retrospectively"
set "PHASEDOC_HOST=127.0.0.1"
set "PHASEDOC_PORT=6793"
if /i "%~1"=="bg" (
  start "" /b pythonw "%USERPROFILE%\phasedoc-tools\cockpit.py"
) else (
  python "%USERPROFILE%\phasedoc-tools\cockpit.py"
)
