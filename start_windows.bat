@echo off
REM Optional Windows convenience launcher. Not required.
REM The canonical start command is:  python run.py
cd /d %~dp0
py run.py %*
if errorlevel 1 python run.py %*
pause
