@echo off
setlocal
cd /d "%~dp0"
set "PCRLIA_PYTHON=python"
if exist "%~dp0.venv\Scripts\python.exe" set "PCRLIA_PYTHON=%~dp0.venv\Scripts\python.exe"
"%PCRLIA_PYTHON%" -m unittest discover -s tests -t .
pause
endlocal
