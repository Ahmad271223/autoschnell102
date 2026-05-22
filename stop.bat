@echo off
title AutoSchnell Stopper
echo.
echo  Beende AutoSchnell Server...
echo.

:: Backend auf Port 8001 beenden
for /f "tokens=5" %%a in ('powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8001 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess"') do (
    if not "%%a"=="" (
        echo  Beende Backend  PID %%a ...
        taskkill /PID %%a /F >nul 2>&1
    )
)

:: Frontend auf Port 3000 beenden
for /f "tokens=5" %%a in ('powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess"') do (
    if not "%%a"=="" (
        echo  Beende Frontend PID %%a ...
        taskkill /PID %%a /F >nul 2>&1
    )
)

echo.
echo  Fertig.
echo.
pause
