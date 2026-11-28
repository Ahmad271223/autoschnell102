# AutoSchnell Helfer: ZIP bauen und auf den Server laden - EIN Befehl (vom PC aus, im Projektordner):
#   powershell -File browser-extension\hochladen.ps1 -Server root@<server>
# Baut die ZIP (bauen.ps1), kopiert sie per scp auf den Server und traegt sie dort im Backend-Container ein
# (werkzeug_hochladen.py). Einmal reicht fuer beide Server (Datei-Speicher S3/R2).
# Vorher den Server deployen (neue Routen), sonst sieht niemand den Browser-Helfer.
# Nur ASCII in dieser Datei: Windows PowerShell 5 liest Skripte ohne BOM als ANSI.
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [string]$Ordner = "/opt/autoschnell",
    [switch]$Force
)
$ErrorActionPreference = "Stop"
# 2.6.3 (Paket 2): nur einen committeten Stand hochladen - ungespeicherte Aenderungen (auch die einer anderen
# Sitzung im selben Ordner) kaemen sonst mit derselben Versionsnummer zu den Kunden. -Force uebergeht das.
$repo = Split-Path $PSScriptRoot -Parent
$offen = & git -C $repo status --porcelain -- browser-extension
if ($offen -and -not $Force) { throw "browser-extension hat ungespeicherte Aenderungen - erst committen (oder -Force)." }
$stand = (& git -C $repo rev-parse --short HEAD)
Write-Host "Stand: $stand"
& (Join-Path $PSScriptRoot "bauen.ps1")
$zip = Join-Path $PSScriptRoot "dist\AutoSchnell-Helfer.zip"
$version = (Get-Content (Join-Path $PSScriptRoot "manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json).version
Write-Host "Kopiere auf $Server ..."
scp $zip "${Server}:/tmp/AutoSchnell-Helfer.zip"
if ($LASTEXITCODE -ne 0) { throw "scp hat nicht geklappt" }
ssh $Server "cd $Ordner && docker compose cp /tmp/AutoSchnell-Helfer.zip backend:/tmp/AutoSchnell-Helfer.zip && docker compose exec -T backend python scripts/werkzeug_hochladen.py /tmp/AutoSchnell-Helfer.zip --werkzeug browser-helfer --version $version"
if ($LASTEXITCODE -ne 0) { throw "Hochladen auf dem Server hat nicht geklappt" }
Write-Host "Fertig: Browser-Helfer $version ist in AutoSchnell unter Programme zum Herunterladen."
