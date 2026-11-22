@echo off
rem Stop only the processes started from THIS folder (other Python /
rem Ollama installations on the machine are left alone).
cd /d "%~dp0"
title Stop GVF System
set "GVF_ROOT=%~dp0"

echo.
echo  Stopping services started from this folder ...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root=$env:GVF_ROOT; $p=Get-Process python,ollama -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($root,[StringComparison]::OrdinalIgnoreCase) }; if(-not $p){ Write-Host '   No running service found.' } else { $p | ForEach-Object { Write-Host ('   stop ' + $_.ProcessName + '  PID ' + $_.Id); Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } }"

echo.
echo  Done.
pause
