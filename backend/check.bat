@echo off
rem ---------------------------------------------------------------------------
rem ASCII only, no chcp, no parenthesised blocks - see the comment in start.bat.
rem Russian text lives in scripts/check.py, where Python writes to the console
rem API directly and the code page does not matter.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

if exist ".venv\.installed" goto have_venv
echo.
echo The environment is not prepared yet. Run start.bat first.
echo.
pause
exit /b 1

:have_venv
call ".venv\Scripts\activate.bat"
python -m scripts.check
echo.
pause
exit /b 0
