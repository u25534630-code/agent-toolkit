@echo off
rem ---------------------------------------------------------------------------
rem Looks for the venv's python, not for the .installed stamp that start.bat
rem writes: an environment built by an older start.bat has no stamp but is
rem perfectly usable, and refusing to run on it sends the user off to reinstall
rem for no reason.
rem
rem ASCII only, no chcp, no parenthesised blocks - see the comment in start.bat.
rem Russian text lives in scripts/check.py.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto have_venv

echo.
echo ============================================
echo   No installation in this folder
echo ============================================
echo.
echo Folder:
echo   %CD%
echo.
if exist ".env" echo The settings file .env is here, but the libraries are missing.
if exist ".env" echo Run start.bat in this folder - it will install them.
if not exist ".env" echo There is no .env here either, so this is probably not the
if not exist ".env" echo folder you set the bot up in. Look for the one where you
if not exist ".env" echo first ran start.bat and answered the setup questions.
echo.
pause
exit /b 1

:have_venv
call ".venv\Scripts\activate.bat"

rem A venv directory proves an interpreter, not the libraries: if pip failed
rem the first time, python.exe is there and every import is not. Probe one
rem package instead of letting the user meet a traceback.
python -c "import pydantic_settings" >nul 2>nul
if errorlevel 1 goto no_libs

python -m scripts.check
echo.
pause
exit /b 0

:no_libs
echo.
echo The libraries are not installed - the first setup did not finish.
echo Run start.bat in this folder, it will install them, then try again.
echo.
pause
exit /b 1
