@echo off
rem Photo Map launcher for Windows. Double-click to start.
rem First run: installs "uv" (a small tool that downloads Python and the app's packages).
setlocal
title Photo Map
cd /d "%~dp0"
set "PYTHONUTF8=1"

echo.
echo   Photo Map
echo   ---------
echo.

rem --- Make sure uv is available -------------------------------------------
where uv >nul 2>nul
if not errorlevel 1 goto :have_uv
if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
    goto :have_uv
)

echo First-time setup: installing uv, which downloads Python for Photo Map.
echo This only happens once and needs an internet connection.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if errorlevel 1 goto :uv_failed
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if errorlevel 1 goto :uv_failed

:have_uv
if not exist ".venv" (
    echo Getting Python and the app's packages ready. The first start takes a minute or two...
    echo.
)

rem --- Start the app (opens your browser) -----------------------------------
uv run --frozen --no-dev python -m photomap
if errorlevel 1 goto :app_failed
exit /b 0

:uv_failed
echo.
echo Couldn't install uv. Check your internet connection and try again.
echo If it keeps failing, send Sam a photo of this window.
echo.
pause
exit /b 1

:app_failed
echo.
echo Photo Map stopped because of a problem.
echo Please send Sam a photo of this window, and the file:
echo     %~dp0data\photo-map.log
echo.
pause
exit /b 1
