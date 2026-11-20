# Builds the FireSky Windows installer (Inno Setup) from the staged package
# tree produced by scripts/build_windows_native.ps1.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/build_windows_installer.ps1
#       [-Arch x64|arm64] [-OutDir dist\native-windows]
#
# Locates ISCC.exe (winget per-user install, Program Files, or -IsccPath).

[CmdletBinding()]
param(
    [ValidateSet("x64", "arm64")]
    [string]$Arch = "x64",
    [string]$OutDir = "",
    [string]$IsccPath = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $Root "dist\native-windows" }
$Version = (Select-String -Path (Join-Path $Root "engine\Cargo.toml") -Pattern '^version = "([^"]*)"').Matches[0].Groups[1].Value
if (-not $Version) { throw "cannot read version from engine/Cargo.toml" }
$Stage = Join-Path $OutDir "stage"
if (-not (Test-Path (Join-Path $Stage "FireSky\FireSky.exe"))) {
    throw "staged package missing: run scripts/build_windows_native.ps1 first ($Stage)"
}

if (-not $IsccPath) {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    $IsccPath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $IsccPath -or -not (Test-Path $IsccPath)) {
    throw "ISCC.exe not found; install Inno Setup 6 (winget install JRSoftware.InnoSetup) or pass -IsccPath."
}

$IssFile = Join-Path $Root "apps\windows\FireSkyInstaller.iss"
& $IsccPath "/DAppVersion=$Version" "/DArch=$Arch" "/DStageDir=$Stage" `
    "/DScriptDir=$(Split-Path -Parent $IssFile)" `
    $IssFile
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed" }

# ISCC 输出在 StageDir（.iss 的 OutputDir）；移到发布目录。
try {
    $Built = Join-Path $Stage "FireSky-$Version-windows-$Arch-setup.exe"
    $Setup = Join-Path $OutDir "FireSky-$Version-windows-$Arch-setup.exe"
    if (-not (Test-Path -LiteralPath $Built)) { throw "installer output missing: $Built" }
    Move-Item -LiteralPath $Built -Destination $Setup -Force
    $Hash = (Get-FileHash -LiteralPath $Setup -Algorithm SHA256).Hash.ToLowerInvariant()
    [System.IO.File]::WriteAllText("$Setup.sha256", "$Hash  FireSky-$Version-windows-$Arch-setup.exe`n", [System.Text.UTF8Encoding]::new($false))
} catch {
    Write-Host $_.InvocationInfo.PositionMessage
    throw
}
Write-Host "installer: $Setup"
Write-Host "sha256:    $Hash"
