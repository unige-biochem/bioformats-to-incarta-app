@echo off
rem  Double-click this. It opens the Bio-Formats to IN Carta window.
rem
rem  The one thing it needs that Windows does not have is uv, which builds the
rem  Python environment; if it is missing, this installs it. Everything else -
rem  Python itself, jgo, Java and the converter - is fetched on first use.
rem
rem  Leave this console window open: it is where a crash would be readable.

setlocal
cd /d "%~dp0"
title Bio-Formats to IN Carta

set "UV="
for /f "delims=" %%I in ('where uv 2^>nul') do if not defined UV set "UV=%%I"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"

if not defined UV (
    echo uv is not installed yet. Installing it -- this takes a moment.
    echo.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
)

if not defined UV (
    echo.
    echo Could not install uv automatically. Install it by hand from
    echo     https://docs.astral.sh/uv/getting-started/installation/
    echo and run this again.
    echo.
    pause
    exit /b 1
)

echo Starting. The window will appear in a few seconds.
"%UV%" run python -m bioformats_to_incarta_app.gui
if errorlevel 1 (
    echo.
    echo The window closed with an error. The reason is above.
    pause
)
