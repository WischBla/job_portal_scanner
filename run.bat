@echo off
REM Optional Windows helper. Not required - "python run.py" works just as well.
cd /d "%~dp0"
py run.py %*
if errorlevel 1 python run.py %*
pause
