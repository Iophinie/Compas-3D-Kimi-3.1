$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host "== Kimi panel for KOMPAS-3D =="

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is not installed. Install it: winget install --id astral-sh.uv --exact"
}

$env:UV_CACHE_DIR = Join-Path $root ".uv-cache"
New-Item -ItemType Directory -Force -Path $env:UV_CACHE_DIR | Out-Null

Write-Host "[1/5] Python environment..."
uv sync

Write-Host "[2/5] KOMPAS MCP server..."
Push-Location (Join-Path $root "vendor\kompas3d-mcp")
uv sync --locked
Pop-Location

Write-Host "[3/5] Panel host..."
$hostExe = Join-Path $root "host\KompasChatPanel.exe"
$hostSrc = Join-Path $root "host\Panel.cs"
$needBuild = $true
if (Test-Path $hostExe) {
    if ((Get-Item $hostExe).LastWriteTime -ge (Get-Item $hostSrc).LastWriteTime) {
        $needBuild = $false
    }
}
if ($needBuild) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root "build_host.ps1")
    if ($LASTEXITCODE -ne 0) { throw "Panel host build failed" }
}

Write-Host "[4/5] KOMPAS menu..."
$rtw = Join-Path $root "host\KimiKompas.rtw"
if (Test-Path $rtw) {
    $addin = "HKCU:\Software\ASCON\KOMPAS-3D\AddIns\KimiKompas"
    New-Item -Path $addin -Force | Out-Null
    Set-ItemProperty -Path $addin -Name "Path" -Value $rtw
    Set-ItemProperty -Path $addin -Name "AutoConnect" -Value 1 -Type DWord
    Write-Host "Registered KOMPAS add-in: $rtw"
} else {
    Write-Host "WARNING: host\KimiKompas.rtw not found; menu item is not installed."
}

# Clean up the experimental .NET COM registration from earlier builds.
$legacyClsid = "HKCU:\Software\Classes\CLSID\{C1B7A0D4-5B2E-4F6A-9C3D-8E7F1A2B3C4D}"
if (Test-Path $legacyClsid) { Remove-Item $legacyClsid -Recurse -Force -ErrorAction SilentlyContinue }
$legacyProgId = "HKCU:\Software\Classes\Kimi.KompasLibrary"
if (Test-Path $legacyProgId) { Remove-Item $legacyProgId -Recurse -Force -ErrorAction SilentlyContinue }

Write-Host "[5/5] Shortcut..."
$appData = Join-Path $env:APPDATA "KompasChat"
New-Item -ItemType Directory -Force -Path $appData | Out-Null

try {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $desktop "Kimi for KOMPAS.lnk"))
    $shortcut.TargetPath = Join-Path $root "run.vbs"
    $shortcut.WorkingDirectory = $root
    $shortcut.IconLocation = "$hostExe,0"
    $shortcut.Save()
} catch {
    Write-Host "Shortcut was not created: $($_.Exception.Message)"
}

Write-Host ""
Write-Host "Install complete."
Write-Host "1. Restart KOMPAS-3D."
Write-Host "2. In KOMPAS open: Prilozheniya -> Kimi dlya KOMPAS -> Otkryt panel Kimi."
Write-Host "3. Or open run.cmd / the desktop shortcut."
Write-Host "4. In the panel click 'Voyti cherez Kimi Code' (no API key needed)."
