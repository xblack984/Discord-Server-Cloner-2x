@echo off
rem Starts the dashboard in its own window, opens it in your browser, then runs the agent in this window.
rem Arguments are passed to the agent, e.g. start_all.bat --once
setlocal
cd /d "%~dp0..\.."
if not exist .venv-xbox\Scripts\python.exe (
  echo Not set up yet. Run xbox_agent\scripts\setup.bat first.
  goto :end
)
if not exist xbox_agent\.env (
  echo xbox_agent\.env is missing. Run xbox_agent\scripts\setup.bat first, then fill it in.
  goto :end
)
start "XboxAgent dashboard" "%~dp0run_dashboard.bat"
echo Starting the dashboard in its own window, please wait...
timeout /t 5 /nobreak >nul
start "" "http://127.0.0.1:8000"
call "%~dp0run_agent.bat" %*
goto :eof

:end
pause
