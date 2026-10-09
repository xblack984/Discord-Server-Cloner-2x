@echo off
rem One-time setup: virtualenv + dependencies + a .env to fill in. The window stays open on purpose.
setlocal
cd /d "%~dp0..\.."
echo Working folder: %CD%
if not exist xbox_agent\requirements.txt (
  echo ERROR: xbox_agent\requirements.txt was not found in this folder.
  echo Extract the whole zip first, do not run this from inside the zip, then run it again.
  goto :fail
)

set "PY="
python -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY (
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
  if not errorlevel 1 set "PY=py -3"
)
if not defined PY (
  echo ERROR: Python 3.11 or newer was not found.
  echo Install it with: winget install -e --id Python.Python.3.12
  echo Then close this window, open a new one, and run setup.bat again.
  goto :fail
)
echo Using: %PY%

if not exist .venv-xbox\Scripts\python.exe %PY% -m venv .venv-xbox
if errorlevel 1 goto :fail
.venv-xbox\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :fail
.venv-xbox\Scripts\python.exe -m pip install -r xbox_agent\requirements.txt -r xbox_agent\requirements-dashboard.txt
if errorlevel 1 goto :fail
if not exist xbox_agent\.env copy xbox_agent\.env.example xbox_agent\.env >nul

echo.
echo DONE. Next steps:
echo   1. Make sure ViGEmBus and Tesseract OCR are installed, see xbox_agent\TRIAL.md
echo   2. Open xbox_agent\.env in Notepad and fill in the tokens and the vision key
echo   3. Run xbox_agent\scripts\run_dashboard.bat
pause
exit /b 0

:fail
echo.
echo FAILED. Read the message above, fix it, and run this file again.
pause
exit /b 1
