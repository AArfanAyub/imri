@echo off
REM ===================================================================
REM  IMRI - start the application
REM
REM  Double-click this file. It finds a Python 3.11+ interpreter, prefers
REM  the windowless pythonw.exe so no console flashes up, and hands over
REM  to main.py.
REM
REM  Optional arguments are forwarded, which is what the smoke test uses:
REM      run.bat --no-window --port 8765
REM      run.bat --check
REM
REM  Exit codes from main.py:
REM      0  clean shutdown
REM      2  another instance is already running
REM      3  startup failed (see data\logs\imri.log)
REM ===================================================================

setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "IMRI_PY="
set "IMRI_PYW="

REM -- 1. An explicit override always wins. ---------------------------
if defined IMRI_PYTHON (
    if exist "%IMRI_PYTHON%" (
        set "IMRI_PY="%IMRI_PYTHON%"
    )
)

REM -- 2. A virtual environment beside this script. -------------------
if not defined IMRI_PY (
    if exist "%~dp0.venv\Scripts\python.exe" (
        set "IMRI_PY="%~dp0.venv\Scripts\python.exe""
    )
)

REM -- 3. The py launcher, which knows about every install. -----------
if not defined IMRI_PY (
    where py >nul 2>&1
    if not errorlevel 1 (
        for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do (
            if exist "%%P" set "IMRI_PY=%%P"
        )
    )
)

REM -- 4. Whatever python.exe is on PATH. ----------------------------
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
    echo   IMRI could not find Python.
    echo.
    echo   Install Python 3.11 or newer from https://www.python.org/downloads/
    echo   and tick "Add python.exe to PATH" during setup, then run this file again.
    echo.
    echo   If Python is already installed somewhere unusual, set the full path first:
    echo       set IMRI_PYTHON=C:\Python311\python.exe
    echo.
    pause
    exit /b 3
)

REM -- 5. Prefer the windowless interpreter for a normal launch. ------
REM       Console output is still wanted for --check and --no-window, so
REM       those two flags keep python.exe.
set "IMRI_USE_CONSOLE=0"
for %%A in (%*) do (
    if /i "%%~A"=="--check" set "IMRI_USE_CONSOLE=1"
    if /i "%%~A"=="--no-window" set "IMRI_USE_CONSOLE=1"
    if /i "%%~A"=="--no-browser" set "IMRI_USE_CONSOLE=1"
    if /i "%%~A"=="--verbose" set "IMRI_USE_CONSOLE=1"
)

if "%IMRI_USE_CONSOLE%"=="0" (
    set "IMRI_PYW=!IMRI_PY:python.exe=pythonw.exe!"
    if "!IMRI_PYW!"=="!IMRI_PY!" set "IMRI_PYW="
    if defined IMRI_PYW if not exist "!IMRI_PYW!" set "IMRI_PYW="
)

if defined IMRI_PYW (
    start "" "!IMRI_PYW!" "%~dp0main.py" %*
    exit /b 0
)

"!IMRI_PY!" "%~dp0main.py" %*
set "IMRI_RC=%ERRORLEVEL%"

if "%IMRI_RC%"=="2" (
    echo.
    echo   IMRI is already running. Look for its window on the taskbar,
    echo   or close it from Task Manager and try again.
    echo.
    if "%IMRI_USE_CONSOLE%"=="0" pause
)

if "%IMRI_RC%"=="3" (
    echo.
    echo   IMRI failed to start. The details are in:
    echo       %~dp0data\logs\imri.log
    echo.
    pause
)

endlocal & exit /b %IMRI_RC%
