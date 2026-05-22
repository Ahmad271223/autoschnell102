@echo off
title AutoSchnell Starter
cd /d "%~dp0"

echo.
echo  ==========================================
echo   AutoSchnell - App starten
echo  ==========================================
echo.

:: ---------- MongoDB ----------
echo [1/3] Pruefe MongoDB...
sc query MongoDB >nul 2>&1
if %errorlevel% == 0 (
    sc start MongoDB >nul 2>&1
    echo       MongoDB Service OK
) else (
    echo       MongoDB Service nicht gefunden - wird uebersprungen.
)

:: ---------- Port-Check per PowerShell (Unicode-sicher) ----------
echo [2/3] Starte Backend  (http://localhost:8001) ...
powershell -NoProfile -Command "if (!(Get-NetTCPConnection -LocalPort 8001 -ErrorAction SilentlyContinue)) { exit 0 } else { exit 1 }" >nul 2>&1
if %errorlevel% == 0 (
    start "AutoSchnell Backend" cmd /k "cd /d "%~dp0backend" && C:\Python314\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8001 --reload --loop asyncio"
) else (
    echo       Port 8001 belegt - Backend laeuft bereits.
)

echo [3/3] Starte Frontend (http://localhost:3000) ...
powershell -NoProfile -Command "if (!(Get-NetTCPConnection -LocalPort 3000 -ErrorAction SilentlyContinue)) { exit 0 } else { exit 1 }" >nul 2>&1
if %errorlevel% == 0 (
    start "AutoSchnell Frontend" cmd /k "cd /d "%~dp0frontend" && yarn start"
) else (
    echo       Port 3000 belegt - Frontend laeuft bereits.
)

echo.
echo  ==========================================
echo   Backend:   http://localhost:8001
echo   Frontend:  http://localhost:3000
echo  ==========================================
echo.
echo  Beide Server laufen in eigenen Fenstern.
echo  Dieses Fenster kann geschlossen werden.
echo.
pause
