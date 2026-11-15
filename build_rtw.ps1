$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $root "kompas-rtw"
$hostDir = Join-Path $root "host"
$vs = "C:\Program Files\Microsoft Visual Studio\18\Community\Common7\Tools\VsDevCmd.bat"
if (-not (Test-Path $vs)) {
    throw "VsDevCmd.bat not found. Install Visual Studio C++ tools or use the shipped host\KimiKompas.rtw."
}

$rtw = Join-Path $hostDir "KimiKompas.rtw"
$res = Join-Path $hostDir "KimiKompas.res"
$cmd = "call `"$vs`" -arch=x64 -no_logo && cd /d `"$src`" && rc /nologo /c 65001 /fo `"$res`" KimiKompas.rc && cl /nologo /LD /MT /utf-8 /EHsc /O2 KimiKompas.cpp `"$res`" /Fe:`"$rtw`" /link /DEF:KimiKompas.def user32.lib shell32.lib"
cmd /c $cmd
if ($LASTEXITCODE -ne 0) { throw "RTW build failed" }
Write-Host "KOMPAS add-in built: $rtw"

