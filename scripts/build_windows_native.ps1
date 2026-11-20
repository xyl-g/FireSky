# Builds the FireSky WinUI native package: Rust engine DLL + WinUI app +
# ecCodes runtime bundle, staged clean and zipped for release distribution.
#
# Windows 口径对齐 scripts/build_macos_native.sh：版本读 engine/Cargo.toml，
# 产物为版本化 zip + SHA256；CI 与本地共用同一打包路径。
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/build_windows_native.ps1
#       [-Arch x64|arm64] [-EccodesRoot DIR] [-VcpkgRoot DIR] [-OutDir DIR]
#       [-SkipZip]
#
# -EccodesRoot must point at an ecCodes runtime bundle (bin/eccodes.dll +
# share/eccodes/definitions + samples), e.g. produced by
#   node apps/shared-web/scripts/bundle-vcpkg-eccodes.mjs
#     <vcpkg>/installed <vcpkg>/ports x64-windows OUT
# With -VcpkgRoot the script runs that bundle step for you.

[CmdletBinding()]
param(
    [ValidateSet("x64", "arm64")]
    [string]$Arch = "",
    [string]$EccodesRoot = $env:FIRESKY_ECCODES_ROOT,
    [string]$VcpkgRoot = "",
    [string]$OutDir = "",
    [switch]$SkipZip
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if (-not $Arch) {
    if ([System.Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture -eq "Arm64") { $Arch = "arm64" } else { $Arch = "x64" }
}
if ($Arch -eq "arm64") {
    $Platform = "ARM64"
    $Rid = "win-arm64"
    $CargoTarget = "aarch64-pc-windows-msvc"
    $Triplet = "arm64-windows"
} else {
    $Platform = "x64"
    $Rid = "win-x64"
    $CargoTarget = "x86_64-pc-windows-msvc"
    $Triplet = "x64-windows"
}
if (-not $OutDir) { $OutDir = Join-Path $Root "dist\native-windows" }

$Version = (Select-String -Path (Join-Path $Root "engine\Cargo.toml") -Pattern '^version = "([^"]*)"').Matches[0].Groups[1].Value
if (-not $Version) { throw "cannot read version from engine/Cargo.toml" }
Write-Host "FireSky $Version — windows/$Arch"

# 1. Rust engine (FFI DLL + CLI binary) for the requested target.
$EngineOut = Join-Path $Root "engine\target\$CargoTarget\release"
cargo build --manifest-path (Join-Path $Root "engine\Cargo.toml") --release --locked `
    --target $CargoTarget --lib --bin firesky-engine
if ($LASTEXITCODE -ne 0) { throw "engine build failed" }
foreach ($name in @("firesky_engine.dll", "firesky-engine.exe")) {
    if (-not (Test-Path (Join-Path $EngineOut $name))) {
        throw "engine artifact missing: $EngineOut\$name"
    }
}

# 2. ecCodes runtime bundle: reuse an existing root or build one via vcpkg.
if (-not $EccodesRoot -and $VcpkgRoot) {
    $EccodesRoot = Join-Path ([System.IO.Path]::GetTempPath()) "firesky-eccodes-$Triplet"
    Write-Host "bundling ecCodes from vcpkg at $VcpkgRoot -> $EccodesRoot"
    node (Join-Path $Root "apps\shared-web\scripts\bundle-vcpkg-eccodes.mjs") `
        (Join-Path $VcpkgRoot "installed") (Join-Path $VcpkgRoot "ports") `
        $Triplet $EccodesRoot
    if ($LASTEXITCODE -ne 0) { throw "ecCodes bundle failed" }
}
if (-not $EccodesRoot) {
    throw "ecCodes bundle required: pass -EccodesRoot / FIRESKY_ECCODES_ROOT, or -VcpkgRoot to build one."
}
foreach ($probe in @("bin\eccodes.dll", "share\eccodes\definitions")) {
    if (-not (Test-Path (Join-Path $EccodesRoot $probe))) {
        throw "ecCodes bundle missing $probe under $EccodesRoot"
    }
}

# 3. WinUI app (self-contained; csproj copies engine binaries, app-data and
#    ecCodes into Resources\).
dotnet build (Join-Path $Root "apps\windows\FireSky\FireSky.csproj") `
    -c Release -p:Platform=$Platform -r $Rid --self-contained true `
    -p:FireSkyEcCodesRoot=$EccodesRoot
if ($LASTEXITCODE -ne 0) { throw "WinUI build failed" }

$BinRoot = Join-Path $Root "apps\windows\FireSky\bin"
$Exe = Get-ChildItem $BinRoot -Recurse -Filter "FireSky.exe" |
    Where-Object { Test-Path (Join-Path $_.Directory.FullName "Resources\firesky_engine.dll") } |
    Where-Object { $_.FullName -match [regex]::Escape($Rid) } |
    Select-Object -First 1
if ($null -eq $Exe) { throw "FireSky.exe with packaged firesky_engine.dll was not produced" }
$AppDir = $Exe.Directory.FullName

# 4. Payload verification: same contract as the CI Verify FFI payload step.
foreach ($required in @(
    "Resources\firesky_engine.dll",
    "Resources\firesky-engine.exe",
    "Resources\app-data\RESOURCE-MANIFEST.json",
    "Resources\eccodes\bin\eccodes.dll",
    "Resources\eccodes\share\eccodes\definitions",
    "Assets\Map\map.html")) {
    if (-not (Test-Path (Join-Path $AppDir $required))) {
        throw "package payload missing: $required"
    }
}
$pythonFiles = Get-ChildItem $AppDir -Recurse -Include "*python*", "*pyside*", "*pyobjc*" -ErrorAction SilentlyContinue
if ($pythonFiles) { throw "Python runtime files found in native package" }

# 5. Stage a clean tree and (unless -SkipZip) produce the release zip + hash.
$Stage = Join-Path $OutDir "stage"
if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
New-Item -ItemType Directory -Path (Join-Path $Stage "FireSky") -Force | Out-Null
Copy-Item "$AppDir\*" (Join-Path $Stage "FireSky") -Recurse -Force
Copy-Item (Join-Path $Root "LICENSE") $Stage -Force

if (-not $SkipZip) {
    $Zip = Join-Path $OutDir "FireSky-$Version-windows-$Arch.zip"
    if (Test-Path $Zip) { Remove-Item $Zip -Force }
    Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $Zip -CompressionLevel Optimal
    $Hash = (Get-FileHash $Zip -Algorithm SHA256).Hash.ToLowerInvariant()
    [System.IO.File]::WriteAllText("$Zip.sha256", "$Hash  FireSky-$Version-windows-$Arch.zip`n", [System.Text.UTF8Encoding]::new($false))
    Write-Host "package: $Zip"
    Write-Host "sha256:  $Hash"
}
Write-Host "staged:  $Stage"
