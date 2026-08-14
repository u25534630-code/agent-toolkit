@echo off
rem ---------------------------------------------------------------------------
rem ASCII only, no chcp, no parenthesised blocks - see the comment in start.bat.
rem Russian text lives in scripts/set_webhook.py, which asks for the new URL:
rem a .bat started by double click gets no arguments.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto have_venv
echo.
echo No installation in this folder:
echo   %CD%
echo.
echo Run start.bat here first.
echo.
pause
exit /b 1

:have_venv
call ".venv\Scripts\activate.bat"
python -m scripts.set_webhook
echo.
pause
exit /b 0
