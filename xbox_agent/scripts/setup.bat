@echo off
rem One-time setup on Windows: virtualenv + dependencies + a .env to fill in.
cd /d "%~dp0..\.."
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 or newer is required: https://www.python.org/downloads/
  exit /b 1
)
if not exist .venv-xbox python -m venv .venv-xbox
call .venv-xbox\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r xbox_agent\requirements.txt -r xbox_agent\requirements-dashboard.txt
if errorlevel 1 exit /b 1
if not exist xbox_agent\.env copy xbox_agent\.env.example xbox_agent\.env >nul
echo.
echo Done. Also install: ViGEmBus (driver) and Tesseract OCR. See xbox_agent\TRIAL.md
echo Next: edit xbox_agent\.env, then run xbox_agent\scripts\run_dashboard.bat
