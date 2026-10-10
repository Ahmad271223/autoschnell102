# AutoPointer-Vergleich: Programm bauen und auf den Server laden - EIN Befehl (vom PC aus, im Projektordner):
#   powershell -ExecutionPolicy Bypass -File autopointer-vergleich\hochladen.ps1 -Server root@<server>
# Wie browser-extension\hochladen.ps1 (Wunsch Ahmad 04.10.2026): baut die EXE (build.ps1, mit Tests),
# kopiert sie per scp auf den Server und traegt sie dort im Backend-Container ein (werkzeug_hochladen.py).
# Einmal reicht fuer beide Server (Datei-Speicher S3/R2). Vorher den Server deployen.
# -OhneBauen: die vorhandene dist\AutoSchnell-Vergleich.exe nehmen (schon gebaut, gleiche Version).
# Nur ASCII in dieser Datei: Windows PowerShell 5 liest Skripte ohne BOM als ANSI.
param(
    [Parameter(Mandatory = $true)][string]$Server,
    [string]$Ordner = "/opt/autoschnell",
    [switch]$OhneBauen
)
$ErrorActionPreference = "Stop"
if (-not $OhneBauen) {
    & (Join-Path $PSScriptRoot "build.ps1")
}
$exe = Join-Path $PSScriptRoot "dist\AutoSchnell-Vergleich.exe"
if (-not (Test-Path $exe)) { throw "Keine Programmdatei unter $exe - erst ohne -OhneBauen aufrufen" }
[xml]$projekt = Get-Content (Join-Path $PSScriptRoot "src\AutoPointerVergleich.csproj") -Raw -Encoding UTF8
$version = ($projekt.Project.PropertyGroup | Where-Object { $_.Version } | Select-Object -First 1).Version
if (-not $version) { throw "Version in AutoPointerVergleich.csproj nicht gefunden" }
$datei = (Get-Item $exe).VersionInfo.ProductVersion.Split('+')[0]
if ($datei -ne $version) { throw "Die Programmdatei ist Version $datei, das Projekt $version - bitte neu bauen (ohne -OhneBauen)" }
Write-Host "Kopiere Programm $version auf $Server ..."
scp $exe "${Server}:/tmp/AutoSchnell-Vergleich.exe"
if ($LASTEXITCODE -ne 0) { throw "scp hat nicht geklappt" }
ssh $Server "cd $Ordner && docker compose cp /tmp/AutoSchnell-Vergleich.exe backend:/tmp/AutoSchnell-Vergleich.exe && docker compose exec -T backend python scripts/werkzeug_hochladen.py /tmp/AutoSchnell-Vergleich.exe --werkzeug autopointer-vergleich --version $version"
if ($LASTEXITCODE -ne 0) { throw "Hochladen auf dem Server hat nicht geklappt" }
Write-Host "Fertig: Programm $version ist in AutoSchnell unter Programme zum Herunterladen."
