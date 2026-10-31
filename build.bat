@echo off
REM ===================================================================
REM  IMRI - build a distributable copy
REM
REM  Double-click for a single dist\IMRI.exe (needs PyInstaller on this
REM  machine). Pass --portable to build a folder that runs from any
REM  Python 3.11 install instead.
REM
REM      build.bat                  one-file exe + smoke test
REM      build.bat --portable       folder build + smoke test
REM      build.bat --clean          wipe dist\ and build\ first
REM      build.bat --no-verify      skip the post-build smoke test
REM
REM  The smoke test starts the build against a temporary data directory
REM  and checks the schema, the seeded admin and the licence state, so a
REM  broken build is caught here rather than at the school.
REM ===================================================================

setlocal EnableExtensions
cd /d "%~dp0"

set "IMRI_PY="

if defined IMRI_PYTHON (
    if exist "%IMRI_PYTHON%" set "IMRI_PY=%IMRI_PYTHON%"
)

if not defined IMRI_PY (
    if exist "%~dp0.venv\Scripts\python.exe" set "IMRI_PY=%~dp0.venv\Scripts\python.exe"
)

if not defined IMRI_PY (
    where py >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do (
            if exist "%%P" set "IMRI_PY=%%P"
        )
    )
)

if not defined IMRI_PY (
    where python >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%P in ('where python') do (
            if not defined IMRI_PY set "IMRI_PY=%%P"
        )
    )
)

if not defined IMRI_PY (
    echo.
    echo   IMRI could not find Python 3.11 or newer.
    echo   Install it from https://www.python.org/downloads/ and tick
    echo   "Add python.exe to PATH", then run this file again.
    echo.
    pause
    exit /b 2
)

echo.
echo   Building IMRI with "%IMRI_PY%"
echo.

"%IMRI_PY%" "%~dp0build_exe.py" %*
set "IMRI_RC=%ERRORLEVEL%"

echo.
if "%IMRI_RC%"=="0" (
    echo   Build finished. Look in the dist folder.
) else (
    echo   Build failed with exit code %IMRI_RC%.
    echo   Read the messages above for the reason.
)
echo.

if "%IMRI_RC%"=="0" (
    REM Only pause on a double-click, so CI can run this unattended.
    echo %CMDCMDLINE% | find /i "/c" >nul && pause
) else (
    pause
)

endlocal & exit /b %IMRI_RC%
