@echo off
rem ---------------------------------------------------------------------------
rem Updates the program files in place, keeping everything the user owns:
rem .env, service-account.json, the database and the installed .venv.
rem
rem Exists because the alternative is "download the ZIP again, unpack it
rem somewhere else, and drag three items across without touching the rest" -
rem a lot of chances to delete the wrong thing.
rem
rem update.bat itself is excluded from the copy: cmd.exe reads a batch file
rem as it runs it, so overwriting the running file mid-execution jumps to
rem garbage. Fetch a new one with the curl line printed at the end.
rem
rem ASCII only, no chcp, no parenthesised blocks - see the comment in start.bat.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"

set "REPO=https://github.com/u25534630-code/home-accounting"
set "BRANCH=claude/bitrix-nnru-integration-kiz73y"
set "WORK=_update_tmp"

echo.
echo ============================================
echo   Recruiter bot - update
echo ============================================
echo.
echo Kept as is: .env, service-account.json, recruiter.db, .venv
echo.

where curl >nul 2>nul
if errorlevel 1 goto no_tools
where robocopy >nul 2>nul
if errorlevel 1 goto no_tools

if exist "%WORK%" rd /s /q "%WORK%"
mkdir "%WORK%"

echo Downloading the latest version...
curl -L -f -s -o "%WORK%\update.zip" "%REPO%/archive/refs/heads/%BRANCH%.zip"
if errorlevel 1 goto download_failed

rem tar ships with Windows 10 and 11 and reads zip; PowerShell is the fallback
rem for anything older or trimmed down.
echo Unpacking...
tar -xf "%WORK%\update.zip" -C "%WORK%" 2>nul
if not errorlevel 1 goto unpacked
powershell -NoProfile -Command "Expand-Archive -Force '%WORK%\update.zip' '%WORK%'"
if errorlevel 1 goto unpack_failed

:unpacked

rem The archive puts everything under one folder whose name is built from the
rem branch; find it instead of guessing it.
set "SRC="
for /d %%d in ("%WORK%\*") do set "SRC=%%~fd\backend"
if not defined SRC goto unpack_failed
if not exist "%SRC%\app" goto unpack_failed

echo Copying...
robocopy "%SRC%" "%CD%" /E /NFL /NDL /NJH /NJS /NP /XF update.bat .env service-account.json recruiter.db /XD .venv >nul
if errorlevel 8 goto copy_failed

rd /s /q "%WORK%"

echo.
echo Done. The program is updated, your settings are untouched.
echo.
echo Next: run check.bat
echo.
pause
exit /b 0

:no_tools
echo This needs curl, tar and robocopy - they ship with Windows 10 and 11.
echo On an older Windows, download the ZIP from GitHub by hand instead.
echo.
pause
exit /b 1

:download_failed
echo Could not download. Check the internet connection.
rd /s /q "%WORK%"
echo.
pause
exit /b 1

:unpack_failed
echo The downloaded archive looks wrong. Try again in a minute.
rd /s /q "%WORK%"
echo.
pause
exit /b 1

:copy_failed
echo Could not copy the files. Close the bot window and run this again.
echo.
pause
exit /b 1
