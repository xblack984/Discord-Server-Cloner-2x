@echo off
rem No arguments: the agent. --check tests every component. --inspect shows what is on screen. --once handles one session.
rem The window stays open when the program stops, so you can read why.
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
.venv-xbox\Scripts\python.exe -m xbox_agent %*
echo.
echo Stopped with exit code %errorlevel%. If that is not what you expected, read the message above.
:end
pause
