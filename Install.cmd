@echo off
rem  Installs Bio-Formats to IN Carta for every user of this machine.
rem  Double-click it: it asks for administrator rights itself.
rem
rem      Install.cmd [folder]     default: %ProgramFiles%\Bio-Formats to IN Carta
rem
rem  Everything goes into that one folder: the app, its own Python, a JDK and
rem  the converter's jars, all downloaded now, so that no user has anything to
rem  fetch. Users can read the folder but not write to it. A shortcut goes into
rem  the Start Menu and onto the Desktop of every user.
rem
rem  Running it again updates the installation - which is also the fix when a
rem  new version needs jars the folder does not have yet.
rem  To uninstall, delete the folder and the two shortcuts.

setlocal
set "SRC=%~dp0"
set "APP=%~1"
if not defined APP set "APP=%ProgramFiles%\Bio-Formats to IN Carta"
title Install Bio-Formats to IN Carta

rem  No folder name inside a ( block ) here: one holding a ")" would end it.

rem  fltmc only works for an administrator, and needs no service running.
fltmc >nul 2>&1
if not errorlevel 1 goto :elevated
echo Asking for administrator rights...
set "B2I_SELF=%~f0"
set "B2I_ARGS=%*"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$a = @{FilePath = $env:B2I_SELF; Verb = 'RunAs'}; if ($env:B2I_ARGS) { $a.ArgumentList = $env:B2I_ARGS }; Start-Process @a"
exit /b
:elevated

rem  An elevated process does not see the drives mapped by the user.
if exist "%SRC%pyproject.toml" goto :readable
echo Cannot read the app from %SRC%
echo If that is a network drive, copy the folder to a local disk first.
goto :failed
:readable

echo Installing into %APP%
echo.
robocopy "%SRC%src" "%APP%\src" /MIR /XD __pycache__ /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :failed
robocopy "%SRC%." "%APP%" pyproject.toml uv.lock README.md LICENSE.txt /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 goto :failed

rem  uv is only needed here, to build the environment. If the machine has none,
rem  it goes into the app's folder rather than onto the administrator's PATH.
set "UV="
for /f "delims=" %%I in ('where uv 2^>nul') do if not defined UV set "UV=%%I"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV if exist "%APP%\uv\uv.exe" set "UV=%APP%\uv\uv.exe"
if defined UV goto :sync
echo Installing uv into %APP%\uv
set "UV_INSTALL_DIR=%APP%\uv"
set "UV_NO_MODIFY_PATH=1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if exist "%APP%\uv\uv.exe" set "UV=%APP%\uv\uv.exe"
if defined UV goto :sync
echo Could not install uv. Install it by hand from
echo     https://docs.astral.sh/uv/getting-started/installation/
echo and run this again.
goto :failed

:sync

rem  The environment, and the Python it runs on, inside the app's folder: by
rem  default uv keeps Python in the administrator's profile, which other users
rem  cannot read. Copies rather than uv's usual hard links into its cache, for
rem  the same reason: a hard link keeps the permissions of the cached file.
rem  Not editable, so that the environment does not depend on src - and so
rem  rebuilt every time: uv would otherwise only notice a new pyproject.toml.
set "UV_PYTHON_INSTALL_DIR=%APP%\python"
set "UV_PYTHON_PREFERENCE=only-managed"
set "UV_LINK_MODE=copy"
set "UV_PROJECT_ENVIRONMENT=%APP%\.venv"
"%UV%" sync --project "%APP%" --frozen --no-dev --no-editable --compile-bytecode --reinstall-package bioformats-to-incarta-app
if errorlevel 1 goto :failed

rem  uv's pythonw.exe in a venv is a console program that starts python.exe, so
rem  a console window would open beside the app's. Python's own windowed venv
rem  launcher - what `python -m venv` puts there - starts pythonw.exe instead.
"%APP%\.venv\Scripts\python.exe" -c "import sys, shutil, pathlib; nt = pathlib.Path(sys.base_prefix, 'Lib', 'venv', 'scripts', 'nt'); shutil.copyfile(next(p for p in (nt / 'venvwlauncher.exe', nt / 'pythonw.exe') if p.exists()), pathlib.Path(sys.prefix, 'Scripts', 'pythonw.exe'))"
if errorlevel 1 goto :failed

rem  The java folder is what tells the app to use shared, read-only caches
rem  (launcher.SHARED); --prepare then fills it: JDK, jars, readers.
echo.
if not exist "%APP%\java" mkdir "%APP%\java"
"%APP%\.venv\Scripts\python.exe" -m bioformats_to_incarta_app.cli --prepare
if errorlevel 1 goto :failed

rem  Whatever was moved in rather than created there - uv's Python, jars renamed
rem  from a download - kept its own permissions. Reset them all to the folder's,
rem  which in Program Files means: users read and run, never write.
echo.
echo Setting permissions...
icacls "%APP%" /reset /T /C /Q >nul
if errorlevel 1 goto :failed

rem  Straight to the environment's pythonw: no console, no uv. Started in the
rem  app's folder, which users cannot write, since python -m imports from there.
set "B2I_HOME=%APP%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; $w = New-Object -ComObject WScript.Shell; foreach ($folder in [Environment]::GetFolderPath('CommonPrograms'), [Environment]::GetFolderPath('CommonDesktopDirectory')) { $s = $w.CreateShortcut((Join-Path $folder 'Bio-Formats to IN Carta.lnk')); $s.TargetPath = Join-Path $env:B2I_HOME '.venv\Scripts\pythonw.exe'; $s.Arguments = '-m bioformats_to_incarta_app.gui'; $s.WorkingDirectory = $env:B2I_HOME; $s.IconLocation = (Join-Path $env:B2I_HOME 'src\bioformats_to_incarta_app\assets\app.ico') + ',0'; $s.Description = 'Convert Bio-Formats images into IN Carta datasets'; $s.Save(); Write-Host ('Shortcut: ' + $s.FullName) }"
if errorlevel 1 goto :failed

echo.
echo Installed. Every user of this machine can now start it from the
echo Start Menu or the Desktop.
echo.
pause
exit /b 0

:failed
echo.
echo The installation did not complete. The reason is above.
echo.
pause
exit /b 1
