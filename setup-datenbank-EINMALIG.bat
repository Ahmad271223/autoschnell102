@echo off
title AutoSchnell - Datenbank einmalig einrichten
chcp 65001 >nul
echo.
echo  ============================================================
echo   AutoSchnell - Datenbank dauerhaft einrichten (EINMALIG)
echo  ============================================================
echo.
echo  Stellt den MongoDB-Dienst dauerhaft auf die bereinigte
echo  Datenbank um (C:\Users\ahmad\AutoSchnellDB) und aktiviert
echo  den Autostart. Danach laeuft die Datenbank bei jedem
echo  Windows-Start automatisch - dieses Skript nur EINMAL noetig.
echo.

REM --- Admin-Rechte pruefen ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo  [FEHLER] Bitte mit RECHTSKLICK ^> "Als Administrator ausfuehren" starten.
    echo.
    pause
    exit /b 1
)

echo  [1/4] Stoppe MongoDB (Dienst + evtl. manuelle Instanzen)...
net stop MongoDB >nul 2>&1
taskkill /F /IM mongod.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo  [2/4] Stelle Datenverzeichnis auf AutoSchnellDB um...
powershell -NoProfile -Command "$c='C:\Program Files\MongoDB\Server\8.2\bin\mongod.cfg'; (Get-Content $c) -replace 'dbPath:.*','dbPath: C:\Users\ahmad\AutoSchnellDB' | Set-Content $c -Encoding utf8"

echo  [3/4] Setze Dienst auf Autostart...
sc config MongoDB start= auto >nul

echo  [4/4] Starte MongoDB-Dienst...
net start MongoDB
if %errorlevel% neq 0 (
    echo  [WARNUNG] Dienst-Start meldete einen Fehler. Pruefe, ob der Ordner
    echo            C:\Users\ahmad\AutoSchnellDB existiert.
) else (
    echo.
    echo  ============================================================
    echo   FERTIG! MongoDB nutzt jetzt die bereinigte Datenbank und
    echo   startet bei jedem Windows-Neustart automatisch.
    echo   Du kannst die App ab sofort einfach mit start.bat starten.
    echo  ============================================================
)
echo.
pause
