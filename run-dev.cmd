@echo off
rem ---------------------------------------------------------------
rem  Source-mode launcher (for people who cloned the repo).
rem  Uses the Python already installed on this machine instead of
rem  the bundled runtime\python. Keep this file ASCII-only.
rem ---------------------------------------------------------------
cd /d "%~dp0app"
title GVF System (source mode)

where python >nul 2>&1
if errorlevel 1 goto nopy

python -c "import numpy" >nul 2>&1
if errorlevel 1 (
  echo  numpy is missing. Installing it now...
  python -m pip install -r "%~dp0requirements.txt"
  if errorlevel 1 goto nonumpy
)

echo.
echo  Starting on http://127.0.0.1:8000
echo  Close this window to stop the server.
echo.
python server.py
goto end

:nopy
echo.
echo  Python was not found on PATH.
echo  Install Python 3.10+ from https://www.python.org/downloads/
echo  and tick "Add python.exe to PATH", then run this file again.
pause
goto end

:nonumpy
echo.
echo  Could not install numpy automatically.
echo  Run this manually:  python -m pip install numpy
pause

:end
