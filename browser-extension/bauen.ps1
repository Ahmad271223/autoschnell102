# AutoSchnell Helfer: ZIP zum Hochladen bauen (Programme -> Browser-Helfer).
#   powershell -File browser-extension\bauen.ps1
# Ergebnis: browser-extension\dist\AutoSchnell-Helfer.zip (manifest.json im obersten Ordner).
# Hochladen auf dem Server wie beim Programm (DEPLOYMENT.md "Programme zum Herunterladen"):
#   docker compose cp AutoSchnell-Helfer.zip backend:/tmp/AutoSchnell-Helfer.zip
#   docker compose exec backend python scripts/werkzeug_hochladen.py /tmp/AutoSchnell-Helfer.zip --werkzeug browser-helfer --version <Version>
# Nur ASCII in dieser Datei: Windows PowerShell 5 liest Skripte ohne BOM als ANSI.
$ErrorActionPreference = "Stop"
$quelle = $PSScriptRoot
$manifest = Get-Content (Join-Path $quelle "manifest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
$dist = Join-Path $quelle "dist"
$arbeit = Join-Path $dist "AutoSchnell-Helfer"
if (Test-Path $arbeit) { Remove-Item $arbeit -Recurse -Force }
New-Item -ItemType Directory -Force $arbeit | Out-Null
foreach ($d in @("manifest.json", "popup.html")) { Copy-Item (Join-Path $quelle $d) $arbeit }
Copy-Item (Join-Path $quelle "icons") (Join-Path $arbeit "icons") -Recurse
# Wunsch Ahmad 04.10.2026 ("darf keiner sehen"): die ausgelieferten Skripte werden verkleinert (Kommentare raus,
# kurze Namen, eine Zeile). Verschleiern (Obfuscator) bewusst NICHT: der Chrome Web Store lehnt verschleierten
# Code ab, Verkleinern ist erlaubt. Das eigentliche Wissen (Auswertung, Regeln, Ampel) liegt nur auf dem Server.
$esbuild = Join-Path $quelle "..\frontend\node_modules\.bin\esbuild.cmd"
if (-not (Test-Path $esbuild)) { throw "esbuild fehlt - einmal 'yarn install' im Ordner frontend ausfuehren." }
foreach ($d in @("background.js", "content.js", "gemeinsam.js", "portal.js", "popup.js")) {
    & $esbuild (Join-Path $quelle $d) --minify --legal-comments=none --target=chrome110 --charset=utf8 `
        --log-level=warning "--outfile=$(Join-Path $arbeit $d)"
    if ($LASTEXITCODE -ne 0) { throw "Verkleinern von $d fehlgeschlagen" }
}
$zip = Join-Path $dist "AutoSchnell-Helfer.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
# ZIP selbst schreiben statt Compress-Archive: das schreibt unter PowerShell 5.1 "icons\icon-16.png" (Backslash),
# die ZIP-Spezifikation und der Chrome Web Store verlangen "/" als Trenner.
Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
$basis = (Get-Item $arbeit).FullName.TrimEnd("\") + "\"
$archiv = [System.IO.Compression.ZipFile]::Open($zip, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    foreach ($f in (Get-ChildItem $arbeit -Recurse -File | Sort-Object FullName)) {
        $name = $f.FullName.Substring($basis.Length).Replace("\", "/")
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $archiv, $f.FullName, $name, [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
    }
} finally { $archiv.Dispose() }
Remove-Item $arbeit -Recurse -Force
Write-Host ("Gebaut: {0}  Version {1}  {2:N0} Bytes" -f $zip, $manifest.version, (Get-Item $zip).Length)
