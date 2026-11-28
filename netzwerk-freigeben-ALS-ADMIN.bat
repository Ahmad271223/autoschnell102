@echo off
title AutoSchnell - Netzwerk-Freigabe
chcp 65001 >nul

echo.
echo  ==========================================
echo   AutoSchnell fuer andere PCs freigeben
echo  ==========================================
echo.

net session >nul 2>&1
if errorlevel 1 (
    echo  [FEHLER] Bitte mit RECHTSKLICK ^> "Als Administrator ausfuehren" starten.
    echo.
    pause
    exit /b 1
)

echo  Lege Firewall-Regel fuer Port 3000 an ^(privates Netz^)...
netsh advfirewall firewall delete rule name="AutoSchnell (Port 3000)" >nul 2>&1
netsh advfirewall firewall add rule name="AutoSchnell (Port 3000)" dir=in action=allow protocol=TCP localport=3000 profile=private
if errorlevel 1 (
    echo  [FEHLER] Regel konnte nicht angelegt werden.
) else (
    echo  Fertig. Andere PCs im gleichen Netz koennen die App jetzt oeffnen.
)

echo.
echo  Adresse fuer den anderen PC:
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    for /f "tokens=1" %%b in ("%%a") do echo    http://%%b:3000
)
echo.
echo  Hinweis: Der andere PC muss im SELBEN WLAN/Netzwerk sein.
echo  Zum Zurueckziehen der Freigabe:
echo    netsh advfirewall firewall delete rule name="AutoSchnell (Port 3000)"
echo.
pause
