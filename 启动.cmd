@echo off
rem ---------------------------------------------------------------
rem  Portable launcher. Keep this file ASCII-only: cmd.exe parses
rem  batch files with the OEM code page, and non-ASCII text breaks
rem  quoting/paths. Chinese instructions live in the .md file.
rem ---------------------------------------------------------------
cd /d "%~dp0"
title GVF System

set "PY=%~dp0runtime\python\python.exe"
set "APP=%~dp0app"
set "OLLAMA=%~dp0model\ollama"

if not exist "%PY%" goto nopy
if not exist "%APP%\server.py" goto noapp

echo.
echo  ==========================================================
echo    Water Surface Profile System  (portable)
echo    Natural-language calculation of 12-type GVF profiles
echo  ==========================================================
echo.

if exist "%OLLAMA%\bin\ollama.exe" (
  echo  [1/3] Starting local model service - a minimized window
  echo        will open. Please do not close it.
  start "Ollama model service" /min "%OLLAMA%\start-model.cmd"
  timeout /t 3 /nobreak >nul
) else (
  echo  [1/3] No local model bundled.
  echo        Rule-based parsing only: calculation, charting, history
  echo        and CSV export still work normally.
)

echo  [2/3] Starting web service and opening the browser ...
start "" "%PY%" "%APP%\open_browser.py"

echo  [3/3] Running. Close this window to stop the web service.
echo.
echo  ----------------------------------------------------------
echo    http://127.0.0.1:8000
echo    If port 8000 is busy it switches to 8001/8002 automatically;
echo    the browser still opens the right address.
echo  ----------------------------------------------------------
echo.

"%PY%" "%APP%\server.py"

echo.
echo  Web service stopped.
pause
goto :eof

:nopy
echo.
echo  [ERROR] runtime\python\python.exe not found.
echo  Copy the WHOLE folder, not just the app subfolder.
pause
exit /b 1

:noapp
echo.
echo  [ERROR] app\server.py not found.
echo  Copy the WHOLE folder.
pause
exit /b 1
