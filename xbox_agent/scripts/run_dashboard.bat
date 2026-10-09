@echo off
rem Starts the web dashboard (default http://127.0.0.1:8000). Leave this window open.
cd /d "%~dp0..\.."
call .venv-xbox\Scripts\activate.bat
python -m xbox_agent.dashboard
