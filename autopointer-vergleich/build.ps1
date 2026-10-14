# Baut AutoSchnell-Vergleich.exe (eine Datei, ohne .NET-Installation lauffaehig)
# Aufruf:  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = 'Stop'
$hier = Split-Path -Parent $MyInvocation.MyCommand.Path

dotnet test (Join-Path $hier 'tests\AutoPointerVergleich.Tests.csproj') -c Release
if ($LASTEXITCODE -ne 0) { throw 'Tests fehlgeschlagen - nichts gebaut.' }

dotnet publish (Join-Path $hier 'src\AutoPointerVergleich.csproj') -c Release -r win-x64 --self-contained true `
    -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true -p:EnableCompressionInSingleFile=true `
    -p:DebugType=none -o (Join-Path $hier 'dist')
if ($LASTEXITCODE -ne 0) { throw 'Bauen fehlgeschlagen.' }

Get-Item (Join-Path $hier 'dist\AutoSchnell-Vergleich.exe') | Select-Object Name, @{n='MB'; e={[math]::Round($_.Length / 1MB, 1)}}
