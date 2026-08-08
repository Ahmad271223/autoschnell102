@echo off
title AutoSchnell Starter
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo  ==========================================
echo   AutoSchnell - App starten
echo  ==========================================
echo.

:: ---------- MongoDB ----------
echo [1/4] Starte MongoDB...
sc query MongoDB >nul 2>&1
if %errorlevel% == 0 (
    net start MongoDB >nul 2>&1
    echo       MongoDB-Dienst gestartet.
) else (
    echo       MongoDB-Dienst nicht gefunden - bitte einmal setup-datenbank-EINMALIG.bat
    echo       als Administrator ausfuehren.
)

:: ---------- Auf MongoDB warten (max. 30 s) ----------
echo [2/4] Warte auf MongoDB (Port 27017)...
set MONGO_OK=0
for /L %%i in (1,1,30) do (
    if !MONGO_OK! == 0 (
        powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 27017 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>&1
        if !errorlevel! == 0 (
            set MONGO_OK=1
            echo       MongoDB ist bereit.
        ) else (
            timeout /t 1 /nobreak >nul
        )
    )
)
if !MONGO_OK! == 0 echo       [WARNUNG] MongoDB nicht erreichbar - Backend wartet selbst weiter.

:: ---------- Backend ----------
echo [3/4] Starte Backend  (http://localhost:8001) ...
powershell -NoProfile -Command "if (!(Get-NetTCPConnection -LocalPort 8001 -ErrorAction SilentlyContinue)) { exit 0 } else { exit 1 }" >nul 2>&1
if %errorlevel% == 0 (
    if exist "C:\Python314\python.exe" (
        start "AutoSchnell Backend" cmd /k "cd /d "%~dp0backend" && C:\Python314\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8001 --reload --loop asyncio"
    ) else (
        start "AutoSchnell Backend" cmd /k "cd /d "%~dp0backend" && python -m uvicorn server:app --host 0.0.0.0 --port 8001 --reload --loop asyncio"
    )
) else (
    echo       Port 8001 belegt - Backend laeuft bereits.
)

:: ---------- Frontend ----------
echo [4/4] Starte Frontend (http://localhost:3000) ...
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
