@echo off
setlocal
cd /d "%~dp0"
set "PCRLIA_PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PCRLIA_PYTHON=%~dp0.venv\Scripts\python.exe"
"%PCRLIA_PYTHON%" "%~dp0scripts\preview_pcr.py" --open --with-mca-times --with-done --with-followup
if errorlevel 1 pause
endlocal
