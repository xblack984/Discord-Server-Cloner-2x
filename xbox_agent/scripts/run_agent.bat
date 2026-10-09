@echo off
rem run_agent.bat            -> the agent, waits for sessions started from the dashboard
rem run_agent.bat --check    -> tests every component (one vision call), presses nothing
rem run_agent.bat --inspect  -> shows what OCR/vision see on the current screen, presses nothing
rem run_agent.bat --once     -> handles one session, then exits
cd /d "%~dp0..\.."
call .venv-xbox\Scripts\activate.bat
python -m xbox_agent %*
