# Serverpruefung vom PC aus - EIN Befehl (Wunsch Ahmad 24.11.2026: "pruefe den Server, ist alles gut, gibt es
# Probleme; schau alle nicht erkannten Inserate an"). Im Projektordner:
#   powershell -ExecutionPolicy Bypass -File deploy\serverpruefung.ps1
#
# NUR LESEN: aendert auf den Servern keine Daten, keine Einstellungen, startet nichts neu.
# Schickt deploy/serverpruefung.sh + die Pruefskripte (Stand dieses PCs, kein git pull noetig) per ssh auf den
# Server, startet sie dort und holt das Ergebnis zurueck - je Server EINE Anmeldung (Kennwort wie beim Hochladen).
#   erster Server (-Server):   Zustand + Systemcheck + alle nicht erkannten Anzeigen (Lesebilder in voller Groesse,
#                              Programm-Vergleiche ohne Erfolg, Leseseiten) - die Datenbank ist gemeinsam
#   weitere Server (-Weitere): nur der Zustand des Servers
# Ergebnis: Ordner Downloads\serverpruefung-<Datum> (je Server ein Unterordner).
param(
    [string]$Server = "root@2.28.66.8",
    [string[]]$Weitere = @("root@167.233.243.23"),
    [int]$Tage = 30,
    [string]$Ziel = "",
    # nur fuer Tests: anderes ssh-Programm
    [string]$Ssh = ""
)
$ErrorActionPreference = "Stop"
$projekt = Split-Path -Parent $PSScriptRoot
$stempel = Get-Date -Format "yyyy-MM-dd_HHmm"
if (-not $Ziel) { $Ziel = Join-Path $env:USERPROFILE "Downloads\serverpruefung-$stempel" }
if (-not $Ssh) {
    $Ssh = Join-Path $env:WINDIR "System32\OpenSSH\ssh.exe"
    if (-not (Test-Path $Ssh)) { $Ssh = "ssh" }
}
$tarExe = Join-Path $env:WINDIR "System32\tar.exe"
if (-not (Test-Path $tarExe)) { throw "tar.exe fehlt ($tarExe) - Windows 10/11 bringt es mit" }

New-Item -ItemType Directory -Force $Ziel | Out-Null
$paket = Join-Path $env:TEMP "autoschnell-pruefung-$stempel"
New-Item -ItemType Directory -Force $paket | Out-Null

# Dateien mit Unix-Zeilenenden einpacken (git auf Windows checkt sie mit CRLF aus - sh stolpert sonst ueber \r)
$dateien = @("deploy\serverpruefung.sh", "backend\scripts\systemcheck.py", "backend\scripts\lesebilder_export.py",
             "backend\scripts\leseseite_analysieren.py")
foreach ($d in $dateien) {
    $quelle = Join-Path $projekt $d
    if (-not (Test-Path $quelle)) { throw "Datei fehlt: $quelle" }
    $text = [System.IO.File]::ReadAllText($quelle).Replace("`r`n", "`n")
    [System.IO.File]::WriteAllText((Join-Path $paket (Split-Path -Leaf $d)), $text, (New-Object System.Text.UTF8Encoding($false)))
}

function Invoke-Pruefung([string]$ziel_host, [string]$art) {
    $name = ($ziel_host -replace '[^A-Za-z0-9._-]', '_')
    $archiv = Join-Path $Ziel "$name.tgz"
    $fern = "rm -rf /tmp/autoschnell-pruefung && mkdir -p /tmp/autoschnell-pruefung && " +
            "tar -xf - -C /tmp/autoschnell-pruefung && sh /tmp/autoschnell-pruefung/serverpruefung.sh $art $Tage < /dev/null"
    # Ueber cmd: die Pipeline bleibt binaer (PowerShell 5.1 wuerde das Archiv als Text umkodieren und zerstoeren)
    $batch = Join-Path $paket "lauf-$name.cmd"
    $zeilen = @(
        "@echo off",
        "`"$tarExe`" -cf - -C `"$paket`" serverpruefung.sh systemcheck.py lesebilder_export.py leseseite_analysieren.py | `"$Ssh`" $ziel_host `"$fern`" > `"$archiv`"",
        "exit /b %ERRORLEVEL%"
    )
    [System.IO.File]::WriteAllLines($batch, $zeilen, [System.Text.Encoding]::ASCII)
    Write-Host ""
    Write-Host "== $ziel_host ($art) - bitte anmelden, falls gefragt; das dauert 1-5 Minuten ..."
    & cmd.exe /c $batch
    $rc = $LASTEXITCODE
    if (-not (Test-Path $archiv) -or (Get-Item $archiv).Length -lt 100) {
        Write-Host "!! $ziel_host lieferte kein Ergebnis (Rueckgabe $rc)" -ForegroundColor Red
        return $false
    }
    # je Server ein eigener Ordner (zwei Server koennen denselben Rechnernamen tragen)
    $ordner = Join-Path $Ziel $name
    New-Item -ItemType Directory -Force $ordner | Out-Null
    & $tarExe -xzf $archiv -C $ordner
    if ($LASTEXITCODE -ne 0) { Write-Host "!! Archiv von $ziel_host nicht lesbar" -ForegroundColor Red; return $false }
    Remove-Item $archiv
    if ($rc -ne 0) { Write-Host "   (Rueckgabe $rc - Ergebnis trotzdem da)" -ForegroundColor Yellow }
    return $true
}

$ok = Invoke-Pruefung $Server "voll"
foreach ($w in $Weitere) {
    if ($w) { $null = Invoke-Pruefung $w "server" }
}
Remove-Item -Recurse -Force $paket

Write-Host ""
if ($ok) {
    Write-Host "FERTIG - Ergebnis in: $Ziel" -ForegroundColor Green
} else {
    Write-Host "Erster Server ohne Ergebnis - Ordner: $Ziel" -ForegroundColor Red
    exit 1
}
