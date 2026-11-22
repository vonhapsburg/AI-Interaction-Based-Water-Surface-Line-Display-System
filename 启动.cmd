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

rem Start the local model window ONLY when config.ini selects provider = ollama.
rem With the DeepSeek API no local model is needed.
set "USE_OLLAMA=0"
if exist "%APP%\config.ini" findstr /i /c:"provider = ollama" "%APP%\config.ini" >nul 2>&1 && set "USE_OLLAMA=1"

if "%USE_OLLAMA%"=="1" if exist "%OLLAMA%\bin\ollama.exe" (
  echo  [1/3] Starting local model service - a minimized window
  echo        will open. Please do not close it.
  start "Ollama model service" /min "%OLLAMA%\start-model.cmd"
  timeout /t 3 /nobreak >nul
) else (
  echo  [1/3] No local model needed.
  echo        Using the DeepSeek API when app\config.ini has an api_key;
  echo        otherwise rule-based parsing only.
  echo        Calculation, charting, history and CSV export always work.
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
