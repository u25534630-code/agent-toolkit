@echo off
rem ---------------------------------------------------------------------------
rem ASCII only, no chcp, no parenthesised blocks.
rem
rem cmd.exe parses a whole if-block before running it, using the console code
rem page. On a Russian Windows that is cp866, so Cyrillic text inside a UTF-8
rem .bat is mis-decoded and the block breaks with "was unexpected at this time".
rem Switching the code page mid-file with chcp makes it worse, not better.
rem Round brackets inside echo have the same effect: cmd reads them as block
rem delimiters. Hence goto instead of blocks, and English messages here.
rem User-facing Russian lives in scripts/setup_env.py, where Python writes to
rem the console API directly and the code page does not matter.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

echo.
echo ============================================
echo   Recruiter bot
echo ============================================
echo.

rem Pick a suitable Python rather than whatever PATH resolves first:
rem ctranslate2, needed by faster-whisper, has no wheels above 3.13.
set "PYCMD="
py -3.13 --version >nul 2>nul && set "PYCMD=py -3.13" && goto found
py -3.12 --version >nul 2>nul && set "PYCMD=py -3.12" && goto found
py -3.11 --version >nul 2>nul && set "PYCMD=py -3.11" && goto found
python --version >nul 2>nul && set "PYCMD=python" && goto found
goto no_python

:found
echo Python: %PYCMD%
echo.

rem The stamp file is written only after pip succeeds. Checking for .venv alone
rem would treat a half-built environment as ready: the venv directory exists as
rem soon as it is created, so a failed install would be skipped on the next run
rem and the bot would start with missing libraries.
if exist ".venv\.installed" goto have_venv

echo Preparing the environment. This takes several minutes.
echo Do not close this window.
echo.
%PYCMD% -m venv .venv
if errorlevel 1 goto venv_failed
call ".venv\Scripts\activate.bat"
echo Installing libraries...
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt
if errorlevel 1 goto pip_failed
echo ok> ".venv\.installed"
goto venv_ready

:have_venv
call ".venv\Scripts\activate.bat"

:venv_ready
if exist ".env" goto run
echo.
echo Settings file not found - starting the setup wizard.
echo.
python -m scripts.setup_env
if not exist ".env" goto no_config

:run
echo.
echo Starting. The first run downloads the speech model - several minutes.
echo The bot works while this window is open. Press Ctrl+C to stop.
echo.
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
exit /b 0

:no_python
echo Python not found.
echo.
echo Install Python 3.13 from python.org/downloads/windows
echo Choose the 64-bit Windows installer.
echo Tick "Add python.exe to PATH" on the first screen of the installer.
echo.
pause
exit /b 1

:venv_failed
echo Could not create the environment.
pause
exit /b 1

:pip_failed
echo Could not install the libraries. Check the internet connection.
pause
exit /b 1

:no_config
echo Setup was not completed.
pause
exit /b 1
