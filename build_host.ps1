$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$hostDir = Join-Path $root "host"
Set-Location $hostDir

$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) {
    throw "C# compiler not found: $csc"
}

$core = Join-Path $hostDir "Microsoft.Web.WebView2.Core.dll"
$winforms = Join-Path $hostDir "Microsoft.Web.WebView2.WinForms.dll"
foreach ($file in @($core, $winforms)) {
    if (-not (Test-Path $file)) {
        throw "Missing WebView2 SDK file: $file"
    }
}

$arguments = @(
    "/nologo",
    "/codepage:65001",
    "/target:winexe",
    "/platform:x64",
    "/out:$hostDir\KompasChatPanel.exe",
    "/r:System.dll",
    "/r:System.Core.dll",
    "/r:System.Drawing.dll",
    "/r:System.Windows.Forms.dll",
    "/r:$core",
    "/r:$winforms",
    "$hostDir\Panel.cs"
)

& $csc @arguments
if ($LASTEXITCODE -ne 0) {
    throw "csc failed with exit code $LASTEXITCODE"
}
Write-Host "Panel host built: $hostDir\KompasChatPanel.exe"
